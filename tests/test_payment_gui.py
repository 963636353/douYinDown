import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication, QWidget

from gui.download_tab import DownloadTab
from payment_kit import LicenseService, PaymentConfig, PaymentError
from payment_kit.identity import DeviceIdentity
from payment_kit.qt import BuyDialog, LicenseController, background_request


class FakeAPI:
    def __init__(self):
        self.valid = False

    def get_license(self, device_id):
        return {"valid": self.valid, "expireTime": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()}

    def get_plans(self):
        return [{"planId": "plan_30d", "name": "30 天授权", "price": "10.00"}]


class PaymentGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = QWidget()
        self.config = PaymentConfig(api_base="http://127.0.0.1:3006/api/pc")

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_slow_request_keeps_gui_timer_running(self):
        ticks = []
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start(5)
        self.assertEqual(background_request(lambda: (time.sleep(0.1), "done")[1]), "done")
        timer.stop()
        self.assertGreater(len(ticks), 3)

    def test_cancel_buy_never_authorizes_download(self):
        controller = LicenseController(self.window, self.config, api=FakeAPI(), device_id="test-install")
        with patch("payment_kit.qt.ask_yes_no", return_value=False):
            self.assertFalse(controller.ensure_authorized("下载"))

    def test_authorization_network_error_does_not_prompt_repurchase(self):
        api = FakeAPI()
        api.get_license = Mock(side_effect=PaymentError("test offline", kind="network"))
        controller = LicenseController(self.window, self.config, api=api, device_id="test-install")
        with patch("payment_kit.qt.info"), patch("payment_kit.qt.ask_yes_no") as ask:
            self.assertFalse(controller.ensure_authorized("下载"))
        ask.assert_not_called()

    def test_disabled_device_is_not_prompted_to_purchase_again(self):
        api = FakeAPI()
        api.get_license = lambda _: {"valid": False, "status": "disabled"}
        controller = LicenseController(self.window, self.config, api=api, device_id="test-install")
        with patch("payment_kit.qt.info"), patch("payment_kit.qt.ask_yes_no") as ask:
            self.assertFalse(controller.ensure_authorized("下载"))
        ask.assert_not_called()

    def test_download_gate_blocks_before_renaming_or_submitting_and_covers_retry(self):
        gate = Mock(return_value=False)
        tab = DownloadTab(self.window, [], lambda _: None, lambda: {}, authorization_check=gate)
        tab.download_list.item_data["item"] = {"filepath": "unchanged.mp4", "platform": "none"}
        with patch.object(tab._executor, "submit") as submit:
            tab._start_download_batch(["item"], "全部下载")
            tab._start_download_batch(["item"], "失败重试", renumber=False)
        self.assertEqual(gate.call_count, 2)
        submit.assert_not_called()
        self.assertEqual(tab.download_list.item_data["item"]["filepath"], "unchanged.mp4")
        self.assertTrue(tab.dl_all_btn.isEnabled())
        tab._executor.shutdown(wait=False)

    def test_verified_authorization_allows_actual_download_dispatch(self):
        tab = DownloadTab(self.window, [], lambda _: None, lambda: {}, authorization_check=lambda _: True)
        tab.download_list.item_data["item"] = {"filepath": "test.mp4", "platform": "none"}
        with patch.object(tab._executor, "submit") as submit:
            tab._start_download_batch(["item"], "全部下载", renumber=False)
        submit.assert_called_once()
        tab._executor.shutdown(wait=False)

    def test_close_during_order_creation_stops_ui_updates_and_polling(self):
        api = FakeAPI()
        service = LicenseService(api, self.config, device_id="test-install")
        dialog = BuyDialog(self.window, service)

        def create(*args):
            time.sleep(0.1)
            return {"success": True, "orderId": "test-order", "qrcode": "invalid-should-not-be-used"}

        api.create_order = create
        QTimer.singleShot(20, dialog.reject)
        with patch("payment_kit.qt.info") as info:
            dialog.create_order()
        self.assertTrue(dialog._closed)
        self.assertIsNone(dialog.pay_thread)
        info.assert_not_called()
        dialog.deleteLater()

    def test_purchase_prefers_server_plan_name_and_falls_back_when_empty(self):
        api = FakeAPI()
        api.get_plans = lambda: [
            {"planId": "plan_9999d", "name": "永久授权", "price": "128.00", "days": 9999},
            {"planId": "plan_30d", "name": "  ", "price": "10.00", "days": 30},
            {"planId": "unnamed", "name": None, "price": "0.01"}]
        dialog = BuyDialog(self.window, LicenseService(api, self.config, device_id="test-install"))
        self.assertEqual(dialog.plan_box.itemText(0), "永久授权  ￥128.00")
        self.assertEqual(dialog.plan_box.currentData(), "plan_9999d")
        self.assertEqual(dialog.plan_box.itemText(1), "30 天授权  ￥10.00")
        self.assertEqual(dialog.plan_box.itemText(2), "未命名套餐  ￥0.01")
        dialog.deleteLater()

    def test_successful_order_with_trial_license_does_not_claim_paid_activation(self):
        api = FakeAPI()
        api.get_license = lambda _: {"valid": True, "status": "trial", "isPaid": False}
        dialog = BuyDialog(self.window, LicenseService(api, self.config, device_id="test-install"))
        thread = Mock()
        dialog.pay_thread = thread
        with patch.object(dialog, "sender", return_value=thread), patch.object(dialog, "_stop_thread"), \
                patch("payment_kit.qt.info") as message:
            dialog.on_pay_success()
        self.assertEqual(message.call_args.args[1], "付款已完成")
        self.assertIn("授权暂未确认", message.call_args.args[2])
        dialog.pay_thread = None
        dialog.deleteLater()

    def test_successful_order_confirms_paid_license_before_success_message(self):
        api = FakeAPI()
        api.get_license = lambda _: {"valid": True, "status": "active", "isPaid": True}
        dialog = BuyDialog(self.window, LicenseService(api, self.config, device_id="test-install"))
        thread = Mock()
        dialog.pay_thread = thread
        with patch.object(dialog, "sender", return_value=thread), patch.object(dialog, "_stop_thread"), \
                patch("payment_kit.qt.info") as message:
            dialog.on_pay_success()
        self.assertEqual(message.call_args.args[1:], ("成功", self.config.paid_message))
        dialog.pay_thread = None
        dialog.deleteLater()

    def test_concurrent_first_install_cannot_overwrite_device_credentials(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "identity.json"
            with ThreadPoolExecutor(max_workers=4) as pool:
                identities = list(pool.map(lambda _: DeviceIdentity(path, self.config.api_base), range(8)))
            self.assertEqual(len({identity.device_id for identity in identities}), 1)
            self.assertEqual(len({identity.token for identity in identities}), 1)

    def test_only_successful_video_downloads_record_count_in_download_worker(self):
        record = Mock()
        tab = DownloadTab(self.window, [], lambda _: None, lambda: {}, download_success_callback=record)
        self.addCleanup(tab._executor.shutdown, wait=False)
        tab.download_done.disconnect()
        tab.download_failed.disconnect()
        completed, failed = [], []
        tab.download_done.connect(lambda *args: completed.append(args))
        tab.download_failed.connect(lambda *args: failed.append(args))
        data = {"platform": "none", "title": "test", "video_url": "https://example.invalid/test",
                "filepath": "test.mp4", "media_type": "video"}
        with patch("gui.download_tab.download_video"):
            tab._download_one("video", data, {})
            tab._download_one("video", data, {})
            tab._download_one("audio", dict(data, filepath="test.mp3", media_type="audio"), {})
            tab._download_one("image", dict(data, filepath="test.jpg", media_type="image"), {})
        with patch("gui.download_tab.download_video", side_effect=RuntimeError("failed")):
            tab._download_one("failed", data, {})
        self.assertEqual(record.call_count, 2)
        self.assertEqual(len(completed), 4)
        self.assertEqual(len(failed), 1)
        record.side_effect = OSError("queue unavailable")
        with patch("gui.download_tab.download_video"):
            tab._download_one("saved", data, {})
        self.assertEqual(len(completed), 5)
        self.assertEqual(len(failed), 1)


if __name__ == "__main__":
    unittest.main()
