import os
import base64
import io
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication, QDialog, QWidget
from PIL import Image

from gui.app import MainWindow
from gui.authorization_tab import AuthorizationDialog, AuthorizationTab
from payment_kit import PaymentConfig, PaymentError


class LicenseAPI:
    def __init__(self, status="trial"):
        self.status = status
        self.get_license = Mock(side_effect=self._license)
        self.get_plans = Mock(return_value=[
            {"planId": "plan_30d", "days": 1, "name": "30 天授权", "price": "0.01"},
            {"planId": "plan_365d", "days": 365, "price": "68.00"},
            {"planId": "plan_999d", "days": 999, "price": "128.00"}])
        image = io.BytesIO()
        Image.new("RGB", (240, 240), "black").save(image, format="PNG")
        self.create_order = Mock(return_value={"success": True, "orderId": "test-order",
                                              "qrcode": base64.b64encode(image.getvalue()).decode("ascii")})
        self.get_order_status = Mock(return_value={"status": "PENDING"})

    def _license(self, _):
        return {"valid": self.status in ("trial", "active"), "status": self.status,
                "isPaid": self.status == "active",
                "expireTime": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()}


class AuthorizationUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = QWidget()
        self.window.resize(980, 720)
        self.window.show()
        self.api = LicenseAPI()
        self.reporter = Mock()
        config = PaymentConfig(api_base="http://127.0.0.1:3006/api/pc", poll_interval=0.01)
        client = (SimpleNamespace(device_id="test-computer-uuid", computer_uuid="test-computer-uuid"),
                  self.api, self.reporter)
        self.patches = [patch("gui.authorization_tab.load_payment_config", return_value=config),
                        patch.object(AuthorizationTab, "_create_client", return_value=client),
                        patch("gui.authorization_tab.info"),
                        patch("gui.app.MainWindow._start_update_check")]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def tearDown(self):
        for tab in self.window.findChildren(AuthorizationTab):
            tab._on_quit()
        if hasattr(self.window, "download_tab"):
            self.window.download_tab._executor.shutdown(wait=False)
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def make_tab(self, status="trial"):
        self.api.status = status
        tab = AuthorizationTab(self.window)
        tab.hide()
        self.app.processEvents()
        self.assertIsNotNone(tab.controller)
        self.assertFalse(tab._busy)
        return tab

    def run_dialog(self, tab, action, invoke=None):
        """在真实模态循环中操作；将异步断言异常带回 unittest 主线程。"""
        errors = []
        original_exec = AuthorizationDialog.exec_

        def inspect(dialog):
            timer = QTimer(dialog)
            timeout = QTimer(dialog)
            timeout.setSingleShot(True)
            timeout.timeout.connect(dialog.reject)
            timeout.start(2500)

            def ready():
                if dialog._busy or not self.api.get_plans.call_count:
                    return
                timer.stop()
                try:
                    action(dialog)
                except Exception as exc:
                    errors.append(exc)
                    dialog.reject()
            timer.timeout.connect(ready)
            timer.start(10)
            result = original_exec(dialog)
            timer.stop()
            timeout.stop()
            return result

        with patch.object(AuthorizationDialog, "exec_", new=inspect), \
                patch("payment_kit.qt.info") as extra_popup:
            result = invoke() if invoke else tab.ensure_authorized("下载")
        extra_popup.assert_not_called()
        if errors:
            raise errors[0]
        return result

    def test_main_window_keeps_authorization_hidden_and_background_services_running(self):
        self.window.close()
        self.window.deleteLater()
        with tempfile.TemporaryDirectory() as directory, \
                patch("gui.settings_tab.CONFIG_PATH", str(Path(directory) / "config.json")), \
                patch("core.paths.app_dir", return_value=directory):
            self.window = MainWindow([])
            self.window.show()
            self.app.processEvents()
            tab = self.window.authorization_tab
            self.assertEqual(self.window.stackedWidget.count(), 4)
            self.assertEqual(self.window.stackedWidget.indexOf(tab), -1)
            self.assertTrue(tab.isHidden())
            self.assertFalse(tab.label_license_status.isVisible())
            self.assertEqual(self.window.about_tab.identity_label.text(), '身份信息：test-computer-uuid')
            self.assertEqual(self.window.download_tab.authorization_check, tab.ensure_authorized)
            tab.record_video_download()
            self.reporter.start.assert_called_once()
            self.reporter.record_video.assert_called_once()
            self.api.get_license.assert_called()

    def test_startup_never_opens_purchase_even_when_expired_or_offline(self):
        with patch.object(AuthorizationTab, "_open_authorization_dialog") as show, \
                patch("gui.authorization_tab.info") as message:
            expired = self.make_tab("expired")
            self.api.get_license.side_effect = PaymentError("offline", kind="network")
            offline = self.make_tab()
        self.assertTrue(expired.isHidden())
        self.assertTrue(offline.isHidden())
        show.assert_not_called()
        message.assert_not_called()

    def test_valid_trial_and_paid_download_without_purchase_window(self):
        tab = self.make_tab()
        with patch.object(tab, "_open_authorization_dialog") as show:
            self.assertTrue(tab.ensure_authorized("下载"))
            self.api.status = "active"
            self.assertTrue(tab.ensure_authorized("失败重试"))
        show.assert_not_called()
        self.assertTrue(tab.isHidden())

    def test_expired_dialog_cancel_blocks_download_and_restores_background(self):
        tab = self.make_tab("expired")
        controller = tab.controller
        inspected = []

        def inspect(dialog):
            inspected.append(dialog)
            self.assertEqual(dialog.service.device_id, "test-computer-uuid")
            self.assertEqual(len(dialog.plan_group.buttons()), 3)
            self.assertIn("30 天授权\n￥0.01", dialog.plan_group.button(0).text())
            self.assertTrue(dialog.buy_button.isEnabled())
            self.assertTrue(tab.isHidden())
            self.assertFalse(tab.ensure_authorized("重复点击"))
            dialog.cancelButton.click()

        self.assertFalse(self.run_dialog(tab, inspect))
        self.assertFalse(self.run_dialog(tab, inspect))
        self.assertEqual(len(inspected), 2)
        self.assertIs(tab.controller, controller)
        self.assertIs(tab.parentWidget(), self.window)
        self.assertIs(controller.window, self.window)
        self.assertTrue(tab.isHidden())
        self.assertIsNone(tab._authorization_dialog)
        tab.record_video_download()
        self.reporter.record_video.assert_called_once()

    def test_refresh_after_payment_automatically_continues_with_fresh_authorization(self):
        tab = self.make_tab("expired")
        checks = []

        def inspect(dialog):
            self.api.status = "active"
            dialog.yesButton.click()
            self.assertTrue(dialog._finishing)
            self.assertIn("支付成功", dialog.status_label.text())
            checks.append(self.api.get_license.call_count)

        self.assertTrue(self.run_dialog(tab, inspect))
        self.assertEqual(self.api.get_license.call_count, checks[0] + 1)
        self.assertTrue(tab.isHidden())
        self.assertIs(tab.controller.window, self.window)

    def test_single_window_selects_plan_displays_qr_and_paid_poll_resumes_original_download(self):
        tab = self.make_tab("expired")
        from gui.download_tab import DownloadTab
        downloads = DownloadTab(self.window, [], lambda _: None, lambda: {}, authorization_check=tab.ensure_authorized)
        self.addCleanup(downloads._executor.shutdown, wait=False)
        downloads.download_list.item_data["item"] = {"filepath": "test.mp4", "platform": "none"}

        def paid(_):
            self.api.status = "active"
            return {"status": "SUCCESS"}
        self.api.get_order_status.side_effect = paid

        def inspect(dialog):
            dialog.plan_group.button(1).click()
            dialog.buy_button.click()
            self.assertTrue(dialog.qr_label.isVisible())
            self.assertFalse(dialog.plan_widget.isVisible())
            self.assertIn("365 天", dialog.order_label.text())
            self.assertIn("微信扫码支付", dialog.status_label.text())
            dialog.create_order()  # 连点不能另建订单
        with patch.object(downloads._executor, "submit") as submit, \
                patch("payment_kit.qt.BuyDialog.exec_") as nested:
            self.run_dialog(tab, inspect, lambda: downloads._start_download_batch(["item"], "全部下载", renumber=False))
        self.api.create_order.assert_called_once_with("test-computer-uuid", "plan_365d")
        nested.assert_not_called()
        submit.assert_called_once()

    def test_server_plan_name_is_preserved_in_selection_and_qr_order(self):
        tab = self.make_tab("expired")
        self.api.get_plans.return_value = [
            {"planId": "plan_9999d", "name": "永久授权", "days": 9999, "price": "128.00"}]

        def inspect(dialog):
            self.assertEqual(dialog.plan_group.button(0).text(), "永久授权\n￥128.00")
            with patch.object(dialog, "_start_polling"):
                dialog.buy_button.click()
            self.assertEqual(dialog.order_label.text(), "已选套餐：永久授权 · ￥128.00")
            self.assertTrue(dialog.qr_label.isVisible())
            dialog.reject()

        self.assertFalse(self.run_dialog(tab, inspect))
        self.api.create_order.assert_called_once_with("test-computer-uuid", "plan_9999d")

    def test_network_disabled_and_unknown_status_do_not_open_purchase(self):
        tab = self.make_tab("expired")
        with patch.object(tab, "_open_authorization_dialog") as show, \
                patch("gui.authorization_tab.info") as message:
            self.api.get_license.side_effect = PaymentError("支付/授权服务连接失败", kind="network")
            self.assertFalse(tab.ensure_authorized("下载"))
            self.assertNotIn("支付", message.call_args.args[2])
            self.api.get_license.side_effect = self.api._license
            self.api.status = "disabled"
            self.assertFalse(tab.ensure_authorized("下载"))
            self.assertEqual(message.call_args.args[1], "设备已禁用")
            self.api.status = "unknown"
            self.assertFalse(tab.ensure_authorized("下载"))
        show.assert_not_called()
        self.assertTrue(tab.isHidden())

    def test_expiry_window_rechecks_before_continue_if_authorization_changes(self):
        tab = self.make_tab("expired")

        def inspect(dialog):
            self.api.status = "active"
            dialog.yesButton.click()
            self.assertTrue(dialog._finishing)
            self.api.status = "expired"
            dialog.finish_timer.stop()
            dialog._continue_download()
            self.assertFalse(dialog._finishing)
            self.assertEqual(dialog.result(), QDialog.Rejected)
            self.assertIn("无法确认", dialog.status_label.text())
            dialog.reject()
        self.assertFalse(self.run_dialog(tab, inspect))

    def test_confirmed_order_without_paid_license_never_resumes_or_prompts_repurchase(self):
        tab = self.make_tab("expired")

        def inspect(dialog):
            self.api.status = "trial"
            thread = Mock()
            dialog.pay_thread = thread
            with patch.object(dialog, "sender", return_value=thread), patch.object(dialog, "_stop_thread"):
                dialog.on_pay_success()
            dialog.pay_thread = None
            self.assertTrue(dialog._paid_seen)
            self.assertFalse(dialog._finishing)
            self.assertIn("授权暂未确认", dialog.status_label.text())
            self.assertFalse(dialog.buy_button.isEnabled())
            self.assertFalse(dialog.change_button.isEnabled())
            dialog.reject()
        self.assertFalse(self.run_dialog(tab, inspect))

    def test_close_during_order_creation_discards_late_response_without_polling(self):
        import time
        tab = self.make_tab("expired")
        result = self.api.create_order.return_value

        def create(*_):
            time.sleep(0.1)
            return result
        self.api.create_order.side_effect = create

        def inspect(dialog):
            QTimer.singleShot(20, dialog.reject)
            dialog.buy_button.click()
            self.assertTrue(dialog._closed)
            self.assertIsNone(dialog.pay_thread)
            self.assertIsNone(dialog._order)
        self.assertFalse(self.run_dialog(tab, inspect))
        self.api.get_order_status.assert_not_called()

    def test_plan_and_order_errors_are_inline_and_can_retry(self):
        tab = self.make_tab("expired")
        plans = self.api.get_plans.return_value
        self.api.get_plans.side_effect = PaymentError("offline", kind="network")

        def inspect(dialog):
            self.assertIn("获取套餐失败", dialog.status_label.text())
            self.assertFalse(dialog.buy_button.isEnabled())
            self.api.get_plans.side_effect = None
            self.api.get_plans.return_value = plans
            dialog.yesButton.click()
            self.assertEqual(len(dialog.plan_group.buttons()), 3)
            self.api.create_order.side_effect = PaymentError("下单超时", kind="network")
            dialog.buy_button.click()
            self.assertIn("生成支付二维码失败", dialog.status_label.text())
            self.assertTrue(dialog.buy_button.isEnabled())
            self.assertTrue(dialog.plan_widget.isEnabled())
            dialog.reject()
        self.assertFalse(self.run_dialog(tab, inspect))

    def test_change_plan_stays_in_window_and_stops_old_polling(self):
        tab = self.make_tab("expired")

        def inspect(dialog):
            dialog.buy_button.click()
            original = dialog.pay_thread
            dialog.change_button.click()
            self.assertIsNone(dialog.pay_thread)
            self.assertTrue(original._cancel.is_set())
            self.assertIsNone(dialog._order)
            self.assertTrue(dialog.plan_widget.isVisible())
            self.assertTrue(dialog.buy_button.isEnabled())
            self.assertFalse(dialog.qr_label.isVisible())
            dialog.reject()
        self.assertFalse(self.run_dialog(tab, inspect))


if __name__ == "__main__":
    unittest.main()
