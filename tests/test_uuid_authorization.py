"""UUID 身份回归：使用临时数据库和模拟支付，绝不访问线上授权服务。"""

import json
import sys
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from fastapi.testclient import TestClient
from sqlmodel import Session, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "服务端"))
from payment_server.app import create_app
from payment_server.config import Settings
from payment_server.models import Device, Order
from payment_server.service import utcnow
from payment_kit import PaymentAPI, PaymentConfig, PaymentError
from payment_kit.identity import DeviceIdentity


class UUIDAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.uuid = uuid.uuid5(uuid.NAMESPACE_URL, "uuid-only-computer").hex
        self.headers = {"X-Device-UUID": self.uuid}
        self.gateway = Mock()
        self.gateway.create.return_value = "weixin://test"
        self.gateway.image.return_value = "data:image/png;base64,dGVzdA=="
        self.gateway.query.side_effect = lambda order: {"out_trade_no": order.order_id, "trade_state": "NOTPAY"}
        self.app = create_app(Settings(database_url=f"sqlite:///{self.folder.as_posix()}/test.db"), self.gateway)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.service = self.app.state.payments

    def register(self, **extra):
        return self.client.post("/api/pc/register", json={"deviceId": self.uuid, **extra})

    def device(self, device_id=None):
        with Session(self.service.db.engine) as session:
            return session.exec(select(Device).where(Device.device_id == (device_id or self.uuid))).one()

    def paid(self, device_id=None):
        device_id = device_id or self.uuid
        with self.service.db.write() as session:
            device = session.exec(select(Device).where(Device.device_id == device_id)).one()
            device.paid_at = utcnow() - timedelta(days=2)
            device.first_seen = utcnow() - timedelta(days=10)
            device.expires_at = utcnow() + timedelta(days=23)
            device.downloaded_video_count = 9
            session.add(device)
        return self.device(device_id)

    def test_uuid_registration_and_restart_need_no_token_and_never_reset_entitlement(self):
        self.assertEqual(self.register().json(), {"deviceId": self.uuid})
        before = self.paid()
        for token in (None, "different-token-" + "a" * 43):
            response = self.register(**({"deviceToken": token} if token else {}))
            self.assertEqual(response.status_code, 201)
            headers = dict(self.headers, **({"Authorization": "Bearer " + token} if token else {}))
            self.assertEqual(self.client.post("/api/pc/startup", json={"uuid": self.uuid}, headers=headers).status_code, 200)
            license = self.client.get("/api/pc/license/" + self.uuid, headers=headers).json()
            self.assertTrue(license["valid"])
            self.assertTrue(license["isPaid"])
            current = self.device()
            self.assertEqual((current.id, current.first_seen, current.expires_at, current.paid_at,
                              current.token_hash, current.downloaded_video_count),
                             (before.id, before.first_seen, before.expires_at, before.paid_at,
                              before.token_hash, before.downloaded_video_count))
        self.assertEqual(self.client.get("/api/pc/stats").json()["total_users"], 1)

    def test_uuid_request_validation_and_other_users_order_remain_checked(self):
        self.register()
        url = "/api/pc/license/" + self.uuid
        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.get(url, headers={"X-Device-UUID": "bad"}).status_code, 422)
        self.assertEqual(self.client.get(url, headers={"X-Device-UUID": uuid.uuid4().hex}).status_code, 422)
        foreign = uuid.uuid5(uuid.NAMESPACE_URL, "foreign-computer").hex
        headers = {"X-Device-UUID": foreign}
        self.assertEqual(self.client.get(url, headers=headers).status_code, 404)
        self.client.post("/api/pc/register", json={"deviceId": foreign})
        self.assertEqual(self.client.get(url, headers=headers).status_code, 403)
        self.assertEqual(self.client.post("/api/pc/create-order", headers=headers,
                        json={"deviceId": self.uuid, "planId": "plan_30d"}).status_code, 403)
        order = self.client.post("/api/pc/create-order", headers=self.headers,
                                json={"deviceId": self.uuid, "planId": "plan_30d"}).json()
        self.assertEqual(self.client.get("/api/pc/order-status/" + order["orderId"], headers=headers).status_code, 404)
        self.assertEqual(self.client.post("/api/pc/startup", json={"uuid": foreign}, headers=self.headers).status_code, 409)
        self.assertEqual(self.client.post("/api/pc/download-count", json={"uuid": foreign, "count": 1},
                                         headers=self.headers).status_code, 403)

    def test_expired_and_blocked_users_do_not_gain_access_by_registering_again(self):
        self.register()
        for blocked in (False, True):
            with self.service.db.write() as session:
                device = session.exec(select(Device).where(Device.device_id == self.uuid)).one()
                device.expires_at = utcnow() - timedelta(days=1)
                device.blocked = blocked
                session.add(device)
            before = self.device()
            self.register()
            license = self.client.get("/api/pc/license/" + self.uuid, headers=self.headers).json()
            self.assertFalse(license["valid"])
            self.assertEqual(license["status"], "disabled" if blocked else "expired")
            self.assertEqual(self.device().expires_at, before.expires_at)
            if blocked:
                self.assertEqual(self.client.post("/api/pc/create-order", headers=self.headers,
                                json={"deviceId": self.uuid, "planId": "plan_30d"}).status_code, 403)

    def test_uuid_concurrent_registration_and_hyphenated_uuid_keep_one_user(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.service.register(self.uuid), range(12)))
        self.assertEqual(results, [{"deviceId": self.uuid}] * 12)
        self.assertEqual(self.client.post("/api/pc/register", json={"deviceId": str(uuid.UUID(self.uuid))}).json(),
                         {"deviceId": self.uuid})
        self.assertEqual(self.client.get("/api/pc/stats").json()["total_users"], 1)

    def test_old_uuid_client_with_lost_token_still_works_without_overwriting_original_hash(self):
        token = "original-" + "a" * 43
        self.register(deviceToken=token)
        before = self.paid()
        wrong = "replacement-" + "b" * 43
        self.assertEqual(self.register(deviceToken=wrong).json(), {"deviceId": self.uuid, "deviceToken": wrong})
        headers = dict(self.headers, Authorization="Bearer " + wrong)
        self.assertEqual(self.client.post("/api/pc/startup", json={"uuid": self.uuid}, headers=headers).status_code, 200)
        self.assertTrue(self.client.get("/api/pc/license/" + self.uuid, headers=headers).json()["isPaid"])
        self.assertEqual(self.device().token_hash, before.token_hash)
        # 更早只有 Bearer 的客户端仍能沿用原令牌。
        self.assertTrue(self.client.get("/api/pc/license/" + self.uuid,
                                       headers={"Authorization": "Bearer " + token}).json()["isPaid"])

    def test_legacy_paid_user_and_orders_migrate_even_when_registration_response_is_lost(self):
        old_id = uuid.uuid4().hex
        self.client.post("/api/pc/register", json={"deviceId": old_id})
        before = self.paid(old_id)
        with self.service.db.write() as session:
            session.add(Order(order_id="old-paid-order", device_id=old_id, plan_id="plan_30d",
                              amount_cents=1000, grant_days=30, status="SUCCESS", created_at=before.first_seen,
                              expires_at=before.expires_at, transaction_id="legacy-transaction"))
        config = PaymentConfig(api_base="http://testserver/api/pc")
        profile, legacy = self.folder / "profile.json", self.folder / "legacy.json"
        # 新位置的无效凭据不能掩盖后来找回的旧随机编号。
        profile.write_text(json.dumps({"apiBase": config.api_base, "deviceId": self.uuid, "deviceToken": "wrong"}))
        legacy.write_text(json.dumps({"apiBase": config.api_base, "deviceId": old_id}))
        lose = True

        def bridge(method, url, **kwargs):
            nonlocal lose
            kwargs.pop("timeout")
            self.assertNotIn("Authorization", kwargs["headers"])
            self.assertNotIn("deviceToken", kwargs.get("json", {}))
            response = self.client.request(method, url.removeprefix("http://testserver"), **kwargs)
            if url.endswith("/register") and lose:
                lose = False
                raise requests.Timeout("lost registration response")
            result = requests.Response()
            result.status_code, result._content, result.url = response.status_code, response.content, url
            return result

        with patch("payment_kit.api.requests.request", side_effect=bridge):
            identity = DeviceIdentity(profile, config.api_base, device_id=self.uuid, legacy_path=legacy)
            with self.assertRaises(PaymentError):
                PaymentAPI(config, identity).startup()
            # 再启动时即使旧凭据全部丢失，也能凭 UUID 找回已经迁移的用户。
            profile.unlink()
            legacy.unlink()
            restored = DeviceIdentity(profile, config.api_base, device_id=self.uuid)
            api = PaymentAPI(config, restored)
            api.startup()
            self.assertTrue(api.get_license(self.uuid)["isPaid"])
            self.assertEqual(api.get_order_status("old-paid-order")["status"], "SUCCESS")
            self.assertEqual(api.report_downloads(self.uuid, 1)["downloaded_video_count"], 10)
        current = self.device()
        self.assertEqual((current.id, current.first_seen, current.expires_at, current.paid_at),
                         (before.id, before.first_seen, before.expires_at, before.paid_at))
        with Session(self.service.db.engine) as session:
            order = session.get(Order, "old-paid-order")
            self.assertEqual((order.device_id, order.transaction_id), (self.uuid, "legacy-transaction"))
        self.assertFalse(profile.exists())
        self.assertEqual(self.client.get("/api/pc/stats").json()["total_users"], 1)

    def test_conflicting_existing_users_are_not_silently_merged_or_reset(self):
        old_id = uuid.uuid4().hex
        self.client.post("/api/pc/register", json={"deviceId": old_id})
        old = self.paid(old_id)
        self.register()
        current = self.device()
        self.assertEqual(self.register(legacyDeviceId=old_id).status_code, 409)
        self.assertEqual((self.device(old_id).id, self.device(old_id).expires_at), (old.id, old.expires_at))
        self.assertEqual((self.device().id, self.device().expires_at), (current.id, current.expires_at))
        foreign = uuid.uuid5(uuid.NAMESPACE_URL, "foreign").hex
        self.assertEqual(self.register(legacyDeviceId=foreign).status_code, 409)

    def test_missing_or_broken_credentials_use_existing_paid_uuid_over_client_api(self):
        self.register()
        before = self.paid()
        config = PaymentConfig(api_base="http://testserver/api/pc")
        path = self.folder / "missing.json"

        def bridge(method, url, **kwargs):
            kwargs.pop("timeout")
            self.assertEqual(kwargs["headers"], self.headers)
            self.assertNotIn("deviceToken", kwargs.get("json", {}))
            response = self.client.request(method, url.removeprefix("http://testserver"), **kwargs)
            result = requests.Response()
            result.status_code, result._content, result.url = response.status_code, response.content, url
            return result

        for content in (None, "corrupted json"):
            if content:
                path.write_text(content)
            with patch("payment_kit.api.requests.request", side_effect=bridge):
                api = PaymentAPI(config, DeviceIdentity(path, config.api_base, device_id=self.uuid))
                api.startup()
                self.assertTrue(api.get_license(self.uuid)["isPaid"])
            self.assertEqual(self.device().expires_at, before.expires_at)


if __name__ == "__main__":
    unittest.main()
