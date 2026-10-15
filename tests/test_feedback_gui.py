"""Real Qt controls with fake services; never contacts feedback or payment servers."""

import os
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QCoreApplication, QEvent, QTimer
from PyQt5.QtGui import QImage
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from core.feedback import FeedbackError, FeedbackLimits, FeedbackReceipt
from gui.feedback_dialogs import FeedbackSuccessDialog
from gui.feedback_tab import FeedbackTab, PROBLEM_PLACEHOLDER, SUGGESTION_PLACEHOLDER


class FeedbackGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.service = Mock()
        self.service.limits = FeedbackLimits()
        self.service.fetch_limits.return_value = self.service.limits
        self.service.submit.return_value = FeedbackReceipt('a' * 32)
        self.tab = FeedbackTab(service=self.service)
        self.tab.resize(820, 640)
        self.message = Mock()
        self.success = Mock()
        self.tab._show_message = self.message
        self.tab._show_success = self.success
        self.tab.show()
        self.wait_for(lambda: self.tab._config_requested and not self.tab._config_loading)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.gates = []

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        self.tab._on_quit()
        self.wait_for(lambda: not self.tab._tasks)
        self.tab.close()
        self.tab.deleteLater()
        self.app.processEvents()

    def wait_for(self, condition, timeout=3):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(5)
        self.assertTrue(condition(), 'Qt operation did not finish in time')

    def add_images(self, count):
        paths = []
        for index in range(count):
            path = Path(self.directory.name) / f'screenshot-{index}.png'
            image = QImage(32, 32, QImage.Format_RGB32)
            image.fill(0x0078d4)
            image.save(str(path))
            paths.append(str(path))
        with patch('gui.feedback_tab.QFileDialog.getOpenFileNames', return_value=(paths, '')):
            self.tab._choose_images()

    def test_open_only_loads_rules_once_and_never_submits(self):
        self.tab.hide()
        self.tab.show()
        self.app.processEvents()
        self.service.fetch_limits.assert_called_once()
        self.service.submit.assert_not_called()

    def test_approved_controls_placeholders_and_mandatory_log(self):
        self.assertEqual(self.tab.description_edit.placeholderText(), PROBLEM_PLACEHOLDER)
        self.assertEqual(self.tab.description_edit.toPlainText(), '')
        self.tab.suggestion_radio.click()
        self.assertEqual(self.tab.description_edit.placeholderText(), SUGGESTION_PLACEHOLDER)
        self.tab.description_edit.setPlainText('保留输入')
        self.tab.problem_radio.click()
        self.assertEqual(self.tab.description_edit.toPlainText(), '保留输入')
        self.assertEqual(self.tab.wechat_edit.width(), 240)
        self.tab.log_check.click()
        self.assertTrue(self.tab.log_check.isChecked())
        self.assertFalse(self.tab.log_check.isEnabled())
        self.assertFalse(hasattr(self.tab, 'preview_log_button'))

    def test_empty_form_never_starts_submission(self):
        self.tab.submit_button.click()
        self.service.submit.assert_not_called()
        self.message.assert_called_once()

    def test_server_limits_change_visible_counter_and_screenshot_quota(self):
        limits = replace(FeedbackLimits(), max_files=2, max_content_length=40)
        self.service.limits = limits
        self.tab._limits_loaded(limits, None)
        self.assertEqual(self.tab.content_count_label.text(), '0 / 40')
        self.add_images(2)
        self.assertEqual(len(self.tab._attachments), 1)
        self.assertFalse(self.tab.add_image_button.isEnabled())
        self.assertIn('1 / 1', self.tab.attachment_hint.text())

    def test_slow_submit_keeps_event_loop_alive_and_blocks_duplicate_clicks(self):
        gate = threading.Event()
        self.gates.append(gate)
        self.service.submit.side_effect = lambda draft: (gate.wait(2), FeedbackReceipt('b' * 32))[1]
        self.tab.description_edit.setPlainText('点击解析后出错')
        self.tab.wechat_edit.setText('contact')
        self.add_images(1)
        ticks = []
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start(5)
        self.tab.submit_button.click()
        self.tab._submit()
        QTest.qWait(60)
        self.assertGreater(len(ticks), 2)
        self.assertTrue(self.tab._busy)
        self.assertFalse(self.tab.description_edit.isEnabled())
        self.assertFalse(self.tab.submit_button.isEnabled())
        self.assertEqual(self.service.submit.call_count, 1)
        self.assertEqual(self.service.submit.call_args.args[0].wechat, 'contact')
        gate.set()
        self.wait_for(lambda: not self.tab._busy)
        timer.stop()
        self.assertEqual(self.tab.description_edit.toPlainText(), '')
        self.assertFalse(self.tab._attachments)
        self.assertEqual(self.tab.wechat_edit.text(), 'contact')
        self.success.assert_called_once_with()
        self.assertEqual(self.tab.feedback_status.text(), '')
        self.assertFalse(self.tab.submit_button.isEnabled())
        self.assertIn('s）', self.tab.submit_button.text())

    def test_failure_preserves_draft_files_and_uses_gui_thread_for_result(self):
        self.tab.description_edit.setPlainText('详细步骤')
        self.tab.wechat_edit.setText('contact')
        self.add_images(1)
        selected = tuple(self.tab._attachments)
        self.service.submit.side_effect = FeedbackError('网络异常，结果未确认', result_unknown=True)
        gui_threads = []
        self.tab._show_message = lambda *args: gui_threads.append(threading.get_ident())
        self.tab.submit_button.click()
        self.wait_for(lambda: not self.tab._busy)
        self.assertEqual(self.tab.description_edit.toPlainText(), '详细步骤')
        self.assertEqual(self.tab.wechat_edit.text(), 'contact')
        self.assertEqual(tuple(self.tab._attachments), selected)
        self.assertEqual(gui_threads, [threading.get_ident()])
        self.success.assert_not_called()
        QTest.qWait(40)
        self.assertEqual(self.service.submit.call_count, 1)

    def test_failure_popup_includes_reason_and_author_wechat(self):
        self.tab.description_edit.setPlainText('详细步骤')
        self.service.submit.side_effect = FeedbackError('网络异常，提交结果未确认。', result_unknown=True)
        self.tab.submit_button.click()
        self.wait_for(lambda: not self.tab._busy)
        self.message.assert_called_once_with(
            '发送失败', '发送失败，失败原因：网络异常，提交结果未确认。可以联系作者微信z851968反馈',
        )

    def test_rate_limit_countdown_expires_without_automatically_resubmitting(self):
        self.tab.description_edit.setPlainText('详细步骤')
        self.service.submit.side_effect = FeedbackError('稍后再试', retry_after=30)
        self.tab.submit_button.click()
        self.wait_for(lambda: not self.tab._busy)
        self.assertFalse(self.tab.submit_button.isEnabled())
        self.tab._cooldown_until = time.monotonic() - 1
        self.tab._update_submit_button()
        self.assertTrue(self.tab.submit_button.isEnabled())
        self.assertEqual(self.service.submit.call_count, 1)
        self.assertEqual(self.tab.description_edit.toPlainText(), '详细步骤')

    def test_config_failure_keeps_manual_submit_available(self):
        self.tab._config_loading = True
        self.tab._limits_loaded(None, FeedbackError('offline'))
        self.assertTrue(self.tab.submit_button.isEnabled())
        self.assertIn('默认限制', self.tab.feedback_status.text())

    def test_shutdown_during_submit_ignores_late_success(self):
        gate = threading.Event()
        self.gates.append(gate)
        self.service.submit.side_effect = lambda draft: (gate.wait(2), FeedbackReceipt('c' * 32))[1]
        self.tab.description_edit.setPlainText('详细步骤')
        self.tab.submit_button.click()
        self.wait_for(lambda: self.service.submit.called)
        self.tab._on_quit()
        gate.set()
        self.wait_for(lambda: not self.tab._tasks)
        self.success.assert_not_called()
        self.assertEqual(self.tab.description_edit.toPlainText(), '详细步骤')

    def test_destroy_page_during_request_does_not_access_deleted_controls(self):
        gate = threading.Event()
        self.gates.append(gate)
        completed = threading.Event()

        def operation():
            gate.wait(2)
            completed.set()
            return FeedbackLimits()

        extra = FeedbackTab(service=self.service)
        extra._run_task(operation, lambda *args: self.fail('Deleted page received a callback'))
        extra.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        gate.set()
        self.wait_for(completed.is_set)
        QTest.qWait(30)

    def test_success_dialog_only_shows_confirmation(self):
        dialog = FeedbackSuccessDialog(self.tab)
        dialog.show()
        self.assertEqual(dialog.yesButton.text(), '知道了')
        self.assertFalse(dialog.cancelButton.isVisible())
        self.assertFalse(hasattr(dialog, 'id_edit'))
        dialog.yesButton.click()
        self.wait_for(lambda: not dialog.isVisible())
        dialog.deleteLater()


if __name__ == '__main__':
    unittest.main()
