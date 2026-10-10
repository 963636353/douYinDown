"""微信 APIv2 Native 下单、查单和通知验签；不输出商户密钥。"""

import base64
import hashlib
import hmac
import io
import secrets
import time
from datetime import timedelta, timezone
from threading import Lock
from xml.etree.ElementTree import Element, SubElement, tostring

import qrcode
import requests
from defusedxml import ElementTree
from PIL import Image, ImageDraw


class PaymentFailure(RuntimeError):
    pass


def sign(params, key, sign_type="MD5"):
    parts = [f"{name}={params[name]}" for name in sorted(params)
             if name != "sign" and params[name] is not None and params[name] != ""]
    message = ("&".join(parts) + "&key=" + key).encode("utf-8")
    if sign_type == "MD5":
        return hashlib.md5(message).hexdigest().upper()
    if sign_type == "HMAC-SHA256":
        return hmac.new(key.encode("utf-8"), message, hashlib.sha256).hexdigest().upper()
    raise PaymentFailure("不支持的签名类型")


def to_xml(params):
    root = Element("xml")
    for key, value in params.items():
        SubElement(root, key).text = str(value)
    return tostring(root, encoding="utf-8")


def parse_xml(raw):
    if len(raw) > 65536:
        raise PaymentFailure("支付报文过大")
    try:
        root = ElementTree.fromstring(raw, forbid_dtd=True)
        if root.tag != "xml" or len(root) > 128:
            raise ValueError()
        result = {}
        for child in root:
            if child.tag in result or len(child) or child.attrib:
                raise ValueError()
            result[child.tag] = child.text or ""
        return result
    except Exception as exc:
        raise PaymentFailure("支付报文格式错误") from exc


def verify(params, key):
    actual = params.get("sign", "")
    expected = sign(params, key, params.get("sign_type") or "MD5")
    if not actual.isascii() or not hmac.compare_digest(expected, actual.upper()):
        raise PaymentFailure("支付签名验证失败")


def qr_image(code_url):
    output = io.BytesIO()
    qrcode.make(code_url).save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


class WeChatGateway:
    def __init__(self, settings):
        self.settings = settings

    def _call(self, endpoint, fields):
        try:
            self.settings.validate_merchant()
        except ValueError as exc:
            raise PaymentFailure(str(exc)) from exc
        params = dict(fields, appid=self.settings.app_id, mch_id=self.settings.merchant_id,
                      nonce_str=secrets.token_hex(16), sign_type="MD5")
        params["sign"] = sign(params, self.settings.api_key)
        try:
            response = requests.post("https://api.mch.weixin.qq.com/pay/" + endpoint,
                                     data=to_xml(params), timeout=(2, 4) if endpoint == "orderquery" else (3, 7),
                                     headers={"Content-Type": "text/xml; charset=utf-8"},
                                     allow_redirects=False)
            response.raise_for_status()
            if response.status_code != 200:
                raise PaymentFailure("微信支付接口返回异常状态")
            data = parse_xml(response.content)
        except requests.RequestException as exc:
            raise PaymentFailure("暂时无法确认微信支付结果，请刷新授权或查询原订单，勿重复付款") from exc
        if data.get("return_code") != "SUCCESS":
            raise PaymentFailure("微信支付接口请求未成功")
        verify(data, self.settings.api_key)
        self.check_merchant(data)
        if data.get("result_code") != "SUCCESS":
            raise PaymentFailure("微信未接受本次请求，请核实商户配置和原订单")
        return data

    def check_merchant(self, data):
        if data.get("appid") != self.settings.app_id or data.get("mch_id") != self.settings.merchant_id:
            raise PaymentFailure("支付商户不匹配")

    def create(self, order, description, client_ip):
        beijing = timezone(timedelta(hours=8))
        data = self._call("unifiedorder", {
            "body": description, "out_trade_no": order.order_id,
            "total_fee": order.amount_cents, "spbill_create_ip": client_ip,
            "notify_url": self.settings.notify_url, "trade_type": "NATIVE",
            "time_expire": order.expires_at.replace(tzinfo=timezone.utc).astimezone(beijing).strftime("%Y%m%d%H%M%S"),
        })
        code_url = data.get("code_url", "")
        if not code_url.startswith("weixin://"):
            raise PaymentFailure("微信未返回有效的付款二维码")
        return code_url

    def query(self, order):
        return self._call("orderquery", {"out_trade_no": order.order_id})

    def notification(self, raw):
        try:
            self.settings.validate_merchant()
        except ValueError as exc:
            raise PaymentFailure(str(exc)) from exc
        data = parse_xml(raw)
        verify(data, self.settings.api_key)
        self.check_merchant(data)
        return data

    def image(self, code_url):
        return qr_image(code_url)


class DemoGateway:
    """仅显式 demo 模式启用；不联网、不收款，没有公网模拟付款接口。"""

    def __init__(self):
        self.started = {}
        self.lock = Lock()

    def create(self, order, description, client_ip):
        with self.lock:
            self.started.setdefault(order.order_id, time.monotonic())
        return "DEMO-NOT-A-PAYMENT-" + order.order_id

    def query(self, order):
        with self.lock:
            started = self.started.get(order.order_id)
        paid = started is not None and time.monotonic() - started >= 6
        return {"return_code": "SUCCESS", "result_code": "SUCCESS",
                "trade_state": "SUCCESS" if paid else "NOTPAY",
                "out_trade_no": order.order_id, "total_fee": str(order.amount_cents),
                "transaction_id": "demo-" + order.order_id}

    def notification(self, raw):
        raise PaymentFailure("演示模式不接收支付通知")

    def image(self, code_url):
        image = Image.new("RGB", (300, 300), "white")
        ImageDraw.Draw(image).multiline_text((45, 100), "LOCAL DEMO\nNO PAYMENT\nAuto success in 6s", fill="black", spacing=12)
        output = io.BytesIO()
        image.save(output, "PNG")
        return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
