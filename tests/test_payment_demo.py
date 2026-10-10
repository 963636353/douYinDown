"""启动本机演示服务进行真实 HTTP 联调；绝不连接微信或旧产品服务。"""

import os
import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import requests

from core.update_checker import fetch_update_info
from payment_kit import LicenseService, PaymentAPI, PaymentConfig
from payment_kit.identity import DeviceIdentity
from payment_kit.usage import UsageReporter


class DemoHTTPTest(unittest.TestCase):
    def test_register_purchase_poll_and_authorize_over_local_http(self):
        root = Path(__file__).resolve().parents[1]
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            environment = dict(os.environ, PAYMENT_MODE="demo", PAYMENT_TRIAL_DAYS="0",
                               PAYMENT_DATABASE_URL=f"sqlite:///{directory.as_posix()}/demo.db")
            with (directory / "server.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen(
                    [sys.executable, "-m", "uvicorn", "main:app", "--app-dir", str(root / "服务端"),
                     "--host", "127.0.0.1", "--port", str(port)],
                    cwd=root, env=environment, stdout=log, stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                session = requests.Session()
                session.trust_env = False
                base = f"http://127.0.0.1:{port}"
                try:
                    for _ in range(100):
                        if process.poll() is not None:
                            self.fail("本机演示服务未成功启动")
                        try:
                            health = session.get(base + "/health", timeout=0.2)
                            if health.status_code == 200:
                                break
                        except requests.RequestException:
                            pass
                        time.sleep(0.1)
                    else:
                        self.fail("演示服务启动超时")
                    self.assertEqual(health.json()["mode"], "demo")
                    manifest = json.loads((root / "服务端" / "videoDown.txt").read_text(encoding="utf-8"))
                    update = session.get(base + "/videoDown.txt", timeout=2)
                    self.assertEqual(update.json(), manifest)
                    self.assertEqual(update.headers["cache-control"], "no-store")
                    parsed = fetch_update_info(base + "/videoDown.txt")
                    self.assertEqual(parsed.version_number, manifest["version_number"])
                    self.assertEqual(parsed.info, manifest["info"])
                    self.assertEqual(parsed.baidu_url, manifest["百度"])
                    self.assertEqual(parsed.quark_url, manifest["夸克"])
                    config = PaymentConfig(api_base=base + "/api/pc")
                    identity = DeviceIdentity(directory / "identity.json", config.api_base,
                                              device_id=uuid.uuid5(uuid.NAMESPACE_URL, "local-demo-computer").hex)
                    api = PaymentAPI(config, identity)
                    service = LicenseService(api, config, identity.device_id)
                    with patch("payment_kit.api.requests.request", side_effect=session.request):
                        self.assertFalse(service.refresh_license())
                        stats = session.get(base + "/api/pc/stats", timeout=2).json()
                        self.assertEqual((stats["total_users"], stats["today_active"]), (1, 1))
                        order = api.create_order(identity.device_id, "plan_30d")
                        self.assertTrue(order["demo"])
                        deadline = time.monotonic() + 15
                        while api.get_order_status(order["orderId"])["status"] != "SUCCESS":
                            self.assertLess(time.monotonic(), deadline, "演示付款查询超时")
                            time.sleep(0.3)
                        self.assertTrue(service.refresh_license())
                        self.assertTrue(service.latest_license["demo"])
                        reporter = UsageReporter(api, identity.device_id, directory / "outbox.db")
                        reporter.record_video()
                        reporter.flush()
                        result = api.report_downloads(identity.device_id, 1)
                        self.assertEqual(result["downloaded_video_count"], 2)
                        reporter.close()
                    self.assertIn("FAIL", session.post(base + "/notify", data=b"<xml/>", timeout=2).text)
                finally:
                    session.close()
                    if os.name == "nt" and process.poll() is None:
                        # Windows venv 启动器还有一个解释器子进程，须清理自己的整棵树。
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                    elif process.poll() is None:
                        process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
