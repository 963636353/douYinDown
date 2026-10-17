"""全部使用临时数据库和模拟微信响应，不访问真实支付服务。"""

import base64
import io
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
from PIL import Image
from sqlmodel import Session, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "服务端"))

from payment_server.app import create_app
from payment_server.config import PLANS, Plan, Settings
from payment_server.models import Device, Order
from payment_server.service import utcnow
from payment_server.wechat import PaymentFailure, WeChatGateway, parse_xml, sign, to_xml, verify
from payment_kit import LicenseService, PaymentAPI, PaymentConfig
from payment_kit.identity import DeviceIdentity


TEST_KEY = "0123456789abcdef0123456789abcdef"


class PaymentServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.settings = Settings(database_url=f"sqlite:///{Path(self.temporary.name).as_posix()}/payment.db",
                                 app_id="wx-test-only", merchant_id="merchant-test-only", api_key=TEST_KEY,
                                 notify_url="https://payment.example/notify", trial_days=0, query_interval_seconds=0)
        self.gateway = WeChatGateway(self.settings)
        self.app = create_app(self.settings, self.gateway)
        self.service = self.app.state.payments
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.paid = {}
        self.request_patch = patch("payment_server.wechat.requests.post", side_effect=self.wechat_response)
        self.requests = self.request_patch.start()
        self.addCleanup(self.request_patch.stop)
        self.device_id = "device-test-0001"
        self.token = self.register(self.device_id)
        self.headers = {"Authorization": "Bearer " + self.token}

    def register(self, device_id):
        result = self.client.post("/api/pc/register", json={"deviceId": device_id})
        self.assertEqual(result.status_code, 201)
        return result.json()["deviceToken"]

    def wechat_response(self, url, **kwargs):
        params = parse_xml(kwargs["data"])
        verify(params, TEST_KEY)
        data = {"return_code": "SUCCESS", "result_code": "SUCCESS",
                "appid": self.settings.app_id, "mch_id": self.settings.merchant_id}
        order_id = params["out_trade_no"]
        if url.endswith("unifiedorder"):
            data["code_url"] = "weixin://wxpay/test-only/" + order_id
        else:
            data.update({"out_trade_no": order_id, "trade_state": "NOTPAY"})
            data.update(self.paid.get(order_id, {}))
        data["sign"] = sign(data, TEST_KEY)
        response = Mock()
        response.status_code, response.content = 200, to_xml(data)
        return response

    def create_order(self, plan_id="plan_30d", device_id=None, headers=None):
        response = self.client.post("/api/pc/create-order", headers=headers or self.headers,
                                    json={"deviceId": device_id or self.device_id, "planId": plan_id})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def notification(self, order_id, **changes):
        with Session(self.service.db.engine) as session:
            order = session.get(Order, order_id)
            amount = order.amount_cents
        data = {"return_code": "SUCCESS", "result_code": "SUCCESS", "appid": self.settings.app_id,
                "mch_id": self.settings.merchant_id, "out_trade_no": order_id,
                "total_fee": str(amount), "transaction_id": "transaction-" + order_id,
                "fee_type": "CNY"}
        data.update(changes)
        data["sign"] = sign(data, TEST_KEY, data.get("sign_type", "MD5"))
        return to_xml(data)

    def notify(self, xml):
        response = self.client.post("/notify", content=xml, headers={"Content-Type": "text/xml"})
        return parse_xml(response.content)["return_code"]

    def license(self):
        return self.client.get("/api/pc/license/" + self.device_id, headers=self.headers).json()

    def device(self):
        with Session(self.service.db.engine) as session:
            return session.exec(select(Device).where(Device.device_id == self.device_id)).one()

    def test_catalog_retains_original_prices_and_999_day_label(self):
        plans = self.client.get("/api/pc/plans").json()
        self.assertEqual([p["price"] for p in plans], ["10.00", "68.00", "128.00"])
        self.assertEqual(plans[-1]["days"], 999)
        self.assertNotIn("永久", plans[-1]["name"])

    def test_registration_stores_hash_and_cannot_reissue_other_devices_token(self):
        self.assertNotEqual(self.device().token_hash, self.token)
        self.assertEqual(len(self.device().token_hash), 64)
        self.assertEqual(self.client.post("/api/pc/register", json={"deviceId": self.device_id}).status_code, 409)
        retry = self.client.post("/api/pc/register", json={"deviceId": self.device_id, "deviceToken": self.token})
        self.assertEqual(retry.status_code, 201)
        self.assertEqual(retry.json()["deviceToken"], self.token)

    def test_permissions_require_token_and_owner_for_license_order_and_status(self):
        self.assertEqual(self.client.get("/api/pc/license/" + self.device_id).status_code, 401)
        foreign = {"Authorization": "Bearer " + self.register("device-test-0002")}
        self.assertEqual(self.client.get("/api/pc/license/" + self.device_id, headers=foreign).status_code, 403)
        self.assertEqual(self.client.post("/api/pc/create-order", headers=foreign,
                                         json={"deviceId": self.device_id, "planId": "plan_30d"}).status_code, 403)
        order = self.create_order()
        self.assertEqual(self.client.get("/api/pc/order-status/" + order["orderId"], headers=foreign).status_code, 404)

    def test_qrcode_is_png_and_wechat_amount_comes_from_server(self):
        order = self.create_order()
        image = Image.open(io.BytesIO(base64.b64decode(order["qrcode"].split(",")[1])))
        self.assertEqual(image.format, "PNG")
        sent = parse_xml(self.requests.call_args.kwargs["data"])
        self.assertEqual(sent["total_fee"], "1000")
        self.assertEqual(sent["trade_type"], "NATIVE")
        self.assertEqual(len(sent["out_trade_no"]), 32)
        response = self.client.post("/api/pc/create-order", headers=self.headers,
                                    json={"deviceId": self.device_id, "planId": "plan_30d", "amount": 1})
        self.assertEqual(response.status_code, 422)

    def test_same_plan_pending_order_is_reused(self):
        first, second = self.create_order(), self.create_order()
        self.assertEqual(first["orderId"], second["orderId"])
        self.assertEqual(self.requests.call_count, 1)

    def test_payment_extends_future_expiry_and_duplicate_notification_does_not_extend_again(self):
        with self.service.db.write() as session:
            device = session.exec(select(Device).where(Device.device_id == self.device_id)).one()
            before = utcnow() + timedelta(days=10)
            device.expires_at = before
            session.add(device)
        order = self.create_order()
        xml = self.notification(order["orderId"])
        self.assertEqual(self.notify(xml), "SUCCESS")
        self.assertEqual(self.device().expires_at, before + timedelta(days=30))
        self.assertEqual(self.notify(xml), "SUCCESS")
        self.assertEqual(self.device().expires_at, before + timedelta(days=30))
        self.assertTrue(self.license()["valid"])
        self.assertTrue(self.license()["isPaid"])

    def test_failed_result_wrong_amount_merchant_app_or_currency_never_grants(self):
        order = self.create_order()
        for fields in ({"result_code": "FAIL"}, {"return_code": "FAIL"}, {"total_fee": "1"},
                       {"appid": "foreign-app"}, {"mch_id": "foreign-merchant"},
                       {"fee_type": "USD"}, {"transaction_id": ""}):
            with self.subTest(fields=fields):
                self.assertEqual(self.notify(self.notification(order["orderId"], **fields)), "FAIL")
                self.assertFalse(self.license()["valid"])

    def test_tampered_signature_and_malformed_xml_never_grants(self):
        order = self.create_order()
        tampered = self.notification(order["orderId"]).replace(b"<total_fee>1000", b"<total_fee>1001")
        for xml in (tampered, b"not xml", b"<xml><sign>a</sign><sign>b</sign></xml>",
                    b'<!DOCTYPE xml [<!ENTITY a "evil">]><xml><sign>&a;</sign></xml>'):
            self.assertEqual(self.notify(xml), "FAIL")
        self.assertFalse(self.license()["valid"])

    def test_hmac_sha256_notification_is_supported(self):
        order = self.create_order()
        self.assertEqual(self.notify(self.notification(order["orderId"], sign_type="HMAC-SHA256")), "SUCCESS")

    def test_order_snapshot_survives_catalog_price_and_days_change(self):
        order = self.create_order()
        original = PLANS["plan_30d"]
        with patch.dict(PLANS, {"plan_30d": Plan("新版", 9900, 99)}):
            now = utcnow()
            self.assertEqual(self.notify(self.notification(order["orderId"])), "SUCCESS")
            self.assertAlmostEqual((self.device().expires_at - now).total_seconds(), 30 * 86400, delta=3)
        self.assertEqual(PLANS["plan_30d"], original)

    def test_concurrent_duplicate_callbacks_grant_once(self):
        order = self.create_order()
        raw = self.notification(order["orderId"])
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.notify(raw), range(8)))
        self.assertEqual(results, ["SUCCESS"] * 8)
        self.assertAlmostEqual((self.device().expires_at - utcnow()).total_seconds(), 30 * 86400, delta=3)

    def test_concurrent_different_orders_accumulate_without_lost_days(self):
        first = self.create_order()
        second = self.create_order("plan_365d")
        raws = [self.notification(first["orderId"]), self.notification(second["orderId"])]
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(self.notify, raws)), ["SUCCESS", "SUCCESS"])
        self.assertAlmostEqual((self.device().expires_at - utcnow()).total_seconds(), 395 * 86400, delta=3)

    def test_commit_failure_rolls_back_order_and_entitlement_then_retry_succeeds(self):
        order = self.create_order()
        raw = self.notification(order["orderId"])
        previous = self.device().expires_at
        with patch("sqlmodel.Session.commit", side_effect=RuntimeError("test commit failure")):
            self.assertEqual(self.notify(raw), "FAIL")
        self.assertEqual(self.device().expires_at, previous)
        with Session(self.service.db.engine) as session:
            self.assertEqual(session.get(Order, order["orderId"]).status, "PENDING")
        self.assertEqual(self.notify(raw), "SUCCESS")

    def test_one_wechat_transaction_cannot_grant_two_orders(self):
        first = self.create_order()
        second = self.create_order("plan_365d")
        self.assertEqual(self.notify(self.notification(first["orderId"], transaction_id="shared-test-id")), "SUCCESS")
        before = self.device().expires_at
        self.assertEqual(self.notify(self.notification(second["orderId"], transaction_id="shared-test-id")), "FAIL")
        self.assertEqual(self.device().expires_at, before)

    def test_order_query_recovers_missing_callback(self):
        order = self.create_order()
        self.paid[order["orderId"]] = {"trade_state": "SUCCESS", "total_fee": "1000",
                                       "transaction_id": "query-test-transaction"}
        self.assertTrue(self.license()["valid"])
        status = self.client.get("/api/pc/order-status/" + order["orderId"], headers=self.headers)
        self.assertEqual(status.json()["status"], "SUCCESS")

    def test_query_timeout_preserves_pending_and_signed_late_callback_can_still_grant(self):
        order = self.create_order()
        with patch("payment_server.wechat.requests.post", side_effect=requests.Timeout()):
            status = self.client.get("/api/pc/order-status/" + order["orderId"], headers=self.headers)
        self.assertEqual(status.json()["status"], "PENDING")
        with self.service.db.write() as session:
            saved = session.get(Order, order["orderId"])
            saved.expires_at = utcnow() - timedelta(hours=1)
            session.add(saved)
        self.assertEqual(self.notify(self.notification(order["orderId"])), "SUCCESS")

    def test_license_only_queries_one_order_per_refresh_to_bound_waiting(self):
        self.create_order()
        self.create_order("plan_365d")
        self.create_order("plan_999d")
        self.requests.reset_mock()
        self.license()
        self.assertEqual(self.requests.call_count, 1)
        self.license()
        self.assertEqual(self.requests.call_count, 2)

    def test_blocked_device_cannot_buy_or_use_but_valid_payment_is_recorded(self):
        order = self.create_order()
        with self.service.db.write() as session:
            device = session.exec(select(Device).where(Device.device_id == self.device_id)).one()
            device.blocked = True
            session.add(device)
        self.assertEqual(self.notify(self.notification(order["orderId"])), "SUCCESS")
        self.assertFalse(self.license()["valid"])
        response = self.client.post("/api/pc/create-order", headers=self.headers,
                                    json={"deviceId": self.device_id, "planId": "plan_30d"})
        self.assertEqual(response.status_code, 403)

    def test_trial_is_granted_once_and_channel_query_cannot_extend_it(self):
        with self.service.db.write() as session:
            device = session.exec(select(Device).where(Device.device_id == self.device_id)).one()
            before = utcnow() + timedelta(days=3)
            device.expires_at = before
            session.add(device)
        for _ in range(2):
            self.client.get("/api/pc/license/" + self.device_id + "?channel=taobao", headers=self.headers)
        self.assertEqual(self.device().expires_at, before)
        self.assertEqual(self.license()["status"], "trial")
        self.assertFalse(self.license()["isPaid"])

    def test_client_registration_payment_and_persistent_identity_round_trip(self):
        path = Path(self.temporary.name) / "client.json"
        config = PaymentConfig(api_base="http://127.0.0.1:3006/api/pc")
        computer_uuid = uuid.uuid5(uuid.NAMESPACE_URL, "round-trip-computer").hex
        identity = DeviceIdentity(path, config.api_base, device_id=computer_uuid)
        api = PaymentAPI(config, identity)
        service = LicenseService(api, config, identity.device_id)

        def bridge(method, url, **kwargs):
            kwargs.pop("timeout")
            result = self.client.request(method, url.removeprefix("http://127.0.0.1:3006"), **kwargs)
            response = requests.Response()
            response.status_code, response._content, response.url = result.status_code, result.content, url
            return response

        with patch("payment_kit.api.requests.request", side_effect=bridge):
            self.assertFalse(service.refresh_license())
            order = api.create_order(identity.device_id, "plan_30d")
            self.assertEqual(self.notify(self.notification(order["orderId"])), "SUCCESS")
            self.assertEqual(api.get_order_status(order["orderId"])["status"], "SUCCESS")
            self.assertTrue(service.refresh_license())
            restored = DeviceIdentity(path, config.api_base, device_id=computer_uuid)
            self.assertEqual(restored.device_id, identity.device_id)
            self.assertFalse(path.exists())
            self.assertTrue(LicenseService(PaymentAPI(config, restored), config, restored.device_id).refresh_license())
        self.assertFalse(path.exists())


class ProtocolTests(unittest.TestCase):
    def test_signature_matches_official_v2_example_and_skips_empty_fields(self):
        # 微信官方 V2 安全规范的公开测试向量，不是真实商户密钥。
        fields = {"appid": "wxd930ea5d5a258f4f", "mch_id": "10000100", "device_info": "1000",
                  "body": "test", "nonce_str": "ibuaiVcKdpRxkhJA"}
        self.assertEqual(sign(fields, "192006250b4c09247ec02edce69f6a2d"), "9A0A8659F005D6984697E2CA0A9CF3B7")
        self.assertEqual(sign(fields, TEST_KEY), sign(dict(fields, empty="", sign="ignored"), TEST_KEY))

    def test_xml_serializer_escapes_product_description(self):
        fields = {"body": "产品 <测试> & 服务", "amount": "1000"}
        self.assertEqual(parse_xml(to_xml(fields)), fields)

    def test_unconfigured_gateway_rejects_notification_without_empty_key_fallback(self):
        gateway = WeChatGateway(Settings())
        with self.assertRaises(PaymentFailure):
            gateway.notification(to_xml({"appid": "", "mch_id": "", "sign": sign({}, "")}))


if __name__ == "__main__":
    unittest.main()
