"""启动公告与真实 Qt 模态窗口顺序；隔离版本请求和授权初始化。"""

import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import QApplication, QDialog

from core.update_checker import APP_VERSION, UpdateInfo
from gui.app import AnnouncementDialog, MainWindow, UpdateDialog


class StartupAnnouncementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = patch('core.paths.app_dir', return_value=self.temp.name)
        self.paths.start()
        self.addCleanup(self.paths.stop)
        # 保留真实主窗口及弹窗，不初始化与本功能无关的授权、下载和反馈服务。
        with patch.object(MainWindow, '_build_ui'), patch.object(MainWindow, '_start_update_check'):
            self.window = MainWindow([])
            self.window.show()
            self.app.processEvents()
        self.timer_patch = patch.object(self.window, '_start_update_check')
        self.timer_patch.start()
        self.addCleanup(self.timer_patch.stop)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def info(self, *, newer=False, forced=False, announcement='公告正文'):
        return UpdateInfo(str(int(APP_VERSION) + 1) if newer else APP_VERSION,
                          'https://example.invalid/baidu', 'https://example.invalid/quark',
                          '升级说明', forced, announcement)

    def test_no_update_shows_announcement_once_without_extra_request(self):
        info = self.info()
        with patch.object(self.window, '_show_update_dialog') as update, \
                patch.object(self.window, '_show_announcement') as notice:
            self.window._handle_update_info(info)
            self.window._handle_update_info(info)
        update.assert_not_called()
        notice.assert_called_once_with('公告正文')

    def test_old_server_without_announcement_keeps_normal_update_behavior(self):
        info = UpdateInfo(str(int(APP_VERSION) + 1), '', '', '旧服务端的升级说明', False)
        with patch.object(self.window, '_show_update_dialog') as update, \
                patch.object(self.window, '_show_announcement') as notice:
            self.window._handle_update_info(info)
        update.assert_called_once_with(info)
        notice.assert_not_called()

    def test_empty_announcement_without_update_shows_nothing(self):
        with patch.object(self.window, '_show_update_dialog') as update, \
                patch.object(self.window, '_show_announcement') as notice:
            self.window._handle_update_info(self.info(announcement=''))
        update.assert_not_called()
        notice.assert_not_called()

    def run_update_action(self, action):
        events, errors = [], []
        original_update_exec = UpdateDialog.exec
        original_notice_exec = AnnouncementDialog.exec
        current_update = []

        def update_exec(dialog):
            current_update.append(dialog)
            events.append('update-open')

            def interact():
                try:
                    self.assertTrue(dialog.isVisible())
                    # 嵌套事件循环收到重复结果时，也不能叠加公告。
                    self.window._handle_update_info(self.info(newer=True))
                    if action == 'skip':
                        dialog.actionButton.click()
                    elif action == 'baidu':
                        dialog.yesButton.click()
                    elif action == 'quark':
                        dialog.cancelButton.click()
                    else:
                        dialog.reject()
                except Exception as exc:
                    errors.append(exc)
                    QDialog.done(dialog, QDialog.Rejected)
            QTimer.singleShot(0, interact)
            timeout = QTimer(dialog)
            timeout.setSingleShot(True)
            timeout.timeout.connect(dialog.reject)
            timeout.start(2500)
            result = original_update_exec(dialog)
            timeout.stop()
            events.append('update-closed')
            return result

        def notice_exec(dialog):
            events.append('announcement-open')
            self.assertFalse(current_update[0].isVisible())
            self.assertEqual(dialog.content.toPlainText(), '公告正文')
            QTimer.singleShot(0, dialog.yesButton.click)
            timeout = QTimer(dialog)
            timeout.setSingleShot(True)
            timeout.timeout.connect(dialog.reject)
            timeout.start(2500)
            result = original_notice_exec(dialog)
            timeout.stop()
            return result

        with patch.object(UpdateDialog, 'exec', new=update_exec), \
                patch.object(AnnouncementDialog, 'exec', new=notice_exec), \
                patch('gui.app.QDesktopServices.openUrl') as open_url:
            self.window._handle_update_info(self.info(newer=True))
        if errors:
            raise errors[0]
        self.assertEqual(events, ['update-open', 'update-closed', 'announcement-open'])
        if action in ('baidu', 'quark'):
            self.assertEqual(open_url.call_args.args[0].toString(), 'https://example.invalid/' + action)
        else:
            open_url.assert_not_called()

    def test_announcement_follows_skipping_update(self):
        self.run_update_action('skip')

    def test_announcement_follows_baidu_download_button(self):
        self.run_update_action('baidu')

    def test_announcement_follows_quark_download_button(self):
        self.run_update_action('quark')

    def test_announcement_follows_closing_update(self):
        self.run_update_action('close')

    def test_forced_update_quits_without_announcement(self):
        with patch.object(UpdateDialog, 'exec', return_value=QDialog.Accepted), \
                patch('gui.app.QApplication.quit') as quit_app, \
                patch.object(self.window, '_show_announcement') as notice:
            self.window._handle_update_info(self.info(newer=True, forced=True))
        quit_app.assert_called_once()
        notice.assert_not_called()

    def test_closing_window_during_update_prevents_late_announcement(self):
        with patch.object(self.window, '_show_update_dialog', side_effect=lambda _: self.window.close()), \
                patch.object(self.window, '_show_announcement') as notice:
            self.window._handle_update_info(self.info(newer=True))
        notice.assert_not_called()

    def test_each_new_window_can_show_same_announcement_again(self):
        with patch.object(self.window, '_show_announcement') as first:
            self.window._handle_update_info(self.info())
        with patch.object(MainWindow, '_build_ui'), patch.object(MainWindow, '_start_update_check'):
            other = MainWindow([])
        try:
            with patch.object(other, '_show_announcement') as second:
                other._handle_update_info(self.info())
            first.assert_called_once()
            second.assert_called_once()
        finally:
            other.close()
            other.deleteLater()

    def test_worker_fetches_once_and_delivers_result_even_without_new_version(self):
        with patch('gui.app.fetch_update_info', return_value=self.info()) as fetch, \
                patch('gui.app.threading.Thread') as thread, \
                patch.object(self.window, '_show_announcement') as notice:
            MainWindow._start_update_check(self.window)
            thread.call_args.kwargs['target']()
        fetch.assert_called_once_with()
        notice.assert_called_once_with('公告正文')

    def test_failed_check_retains_exit_flow_and_does_not_show_notice(self):
        errors = []
        self.window.update_check_failed.connect(errors.append)
        with patch('gui.app.fetch_update_info', side_effect=RuntimeError('offline')), \
                patch('gui.app.threading.Thread') as thread, \
                patch('gui.app.UpdateCheckFailedDialog') as dialog, \
                patch('gui.app.QApplication.quit') as quit_app, \
                patch.object(self.window, '_show_announcement') as notice:
            MainWindow._start_update_check(self.window)
            thread.call_args.kwargs['target']()
        self.assertEqual(errors, ['offline'])
        dialog.return_value.exec.assert_called_once()
        quit_app.assert_called_once()
        notice.assert_not_called()

    def test_long_notice_is_plain_readonly_scrollable_and_closable(self):
        text = '<b>软件公告</b>\n' + '\n'.join(f'第 {i} 行公告：较长的文字说明。' for i in range(200))
        dialog = AnnouncementDialog(text, self.window)
        dialog.show()
        self.app.processEvents()
        try:
            self.assertEqual(dialog.title.text(), '软件公告')
            self.assertEqual(dialog.content.toPlainText(), text)
            self.assertTrue(dialog.content.isReadOnly())
            self.assertFalse(dialog.content.openExternalLinks())
            self.assertFalse(dialog.content.openLinks())
            self.assertEqual(dialog.content.horizontalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
            self.assertGreater(dialog.content.verticalScrollBar().maximum(), 0)
            self.assertLess(dialog.widget.height(), self.window.height())
            self.assertEqual(dialog.yesButton.text(), '我知道了')
            self.assertTrue(dialog.cancelButton.isHidden())
            self.assertTrue(dialog.isClosableOnMaskClicked())
            QTimer.singleShot(0, dialog.yesButton.click)
            self.assertEqual(dialog.exec(), QDialog.Accepted)
        finally:
            dialog.deleteLater()


if __name__ == '__main__':
    unittest.main()
