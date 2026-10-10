"""PyQt5 + Fluent Widgets 接入层，交互沿用原项目的购买弹窗。"""

import base64
import threading
import time
from datetime import datetime

from PyQt5.QtCore import QEventLoop, QThread, Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QLabel
from qfluentwidgets import BodyLabel, ComboBox, MessageBoxBase, PrimaryPushButton, StrongBodyLabel

from .api import PaymentAPI, PaymentError
from .dialogs import ask_yes_no, info
from .license import LicenseService


class RequestThread(QThread):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self.function()
        except Exception as exc:
            self.error = exc


def wait_for_thread(thread):
    loop = QEventLoop()
    thread.finished.connect(loop.quit)
    if thread.isRunning():
        loop.exec_()
    thread.wait()


def background_request(function):
    # 保持同步调用约定，但网络在工作线程运行，等待期间 GUI 继续处理事件。
    thread = RequestThread(function)
    loop = QEventLoop()
    thread.finished.connect(loop.quit)
    thread.start()
    loop.exec_()
    thread.wait()
    result, error = thread.result, thread.error
    thread.deleteLater()
    if error is not None:
        raise error
    return result


def format_expiry(value):
    if not value:
        return "未提供"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        return parsed.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return str(value)


def format_plan_name(plan):
    name = plan.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    days = plan.get("days")
    return f"{days} 天授权" if type(days) is int and days > 0 else "未命名套餐"


class PayThread(QThread):
    success = pyqtSignal()
    ended = pyqtSignal(str)
    network_issue = pyqtSignal(str)

    def __init__(self, api, order_id, config, parent=None):
        super().__init__(parent)
        self.api, self.order_id, self.config = api, order_id, config
        self._cancel = threading.Event()

    def stop(self):
        self._cancel.set()

    def run(self):
        deadline = time.monotonic() + self.config.order_wait_seconds
        while not self._cancel.is_set():
            if time.monotonic() >= deadline:
                self.ended.emit("等待支付超时。若已付款，请关闭后刷新授权，勿重复付款。")
                return
            try:
                data = self.api.get_order_status(self.order_id)
                if self._cancel.is_set():
                    return
                if data["status"] == "SUCCESS":
                    self.success.emit()
                    return
                # 原接口只确认 SUCCESS；这些终态需要新服务端与客户端约定。
                if data["status"] in {"CLOSED", "EXPIRED", "FAILED", "CANCELLED"}:
                    self.ended.emit("订单已关闭、过期或失败。若已扣款，请先核实原订单。")
                    return
            except PaymentError as exc:
                if self._cancel.is_set():
                    return
                if exc.kind == "network":
                    self.network_issue.emit("网络暂时异常，正在继续查询付款结果…")
                else:
                    self.ended.emit(str(exc) + "。若已付款，请先刷新授权或联系客服。")
                    return
            self._cancel.wait(min(self.config.poll_interval, max(0, deadline - time.monotonic())))


class BuyDialog(MessageBoxBase):
    license_updated = pyqtSignal()

    def __init__(self, parent, service):
        super().__init__(parent)
        self.service = service
        self.api, self.config = service.api, service.config
        self.pay_thread = None
        self._closed = False
        self._creating = False
        self._init_ui()
        self._load_plans()
        self._refresh_license_label()

    def _init_ui(self):
        self.title = StrongBodyLabel(f"购买授权 · {self.config.product_name}", self)
        self.sub_title = BodyLabel("请选择套餐并扫码支付", self)
        self.plan_box = ComboBox(self)
        self.buy_btn = PrimaryPushButton("立即购买", self)
        self.buy_btn.clicked.connect(self.create_order)
        self.qr_label = QLabel(self)
        self.qr_label.setAlignment(Qt.AlignCenter)
        self.qr_label.setMinimumHeight(240)
        self.status_label = BodyLabel("授权状态检测中…", self)
        self.status_label.setWordWrap(True)
        for widget in (self.title, self.sub_title, self.plan_box, self.buy_btn,
                       self.qr_label, self.status_label):
            self.viewLayout.addWidget(widget)
        self.hideYesButton()
        self.cancelButton.setText("关闭")
        self.widget.setMinimumWidth(440)

    def _load_plans(self):
        try:
            plans = background_request(self.api.get_plans)
            if self._closed:
                return
            for plan in plans:
                name = format_plan_name(plan)
                self.plan_box.addItem(
                    f"{name}  ￥{plan.get('price', '?')}",
                    userData=plan["planId"],
                )
            self.buy_btn.setEnabled(bool(plans))
            if not plans:
                info(self, "提示", "暂无可购买的套餐")
        except PaymentError as exc:
            self.buy_btn.setEnabled(False)
            info(self, "获取套餐失败", str(exc))

    def _refresh_license_label(self):
        valid = background_request(self.service.refresh_license)
        if self._closed:
            return valid
        data = self.service.latest_license
        if valid:
            suffix = "（网络异常，暂用已验证状态）" if data.get("cached") else ""
            state = "试用中" if data.get("status") == "trial" else "已授权"
            demo = "本地演示，不收款。" if data.get("demo") else ""
            self.status_label.setText(f"{demo}{state}，到期：{format_expiry(data.get('expireTime'))}{suffix}")
        elif data.get("error"):
            self.status_label.setText(data.get("message", "无法连接授权服务"))
        else:
            disabled = data.get("status") == "disabled"
            self.status_label.setText("设备已禁用，请联系客服" if disabled else "当前设备未授权")
            if disabled:
                self.buy_btn.setEnabled(False)
        return valid

    def create_order(self):
        if self.pay_thread is not None or self._creating or self._closed:
            return
        plan_id = self.plan_box.currentData()
        if plan_id is None:
            info(self, "提示", "请先选择套餐")
            return
        self.buy_btn.setEnabled(False)
        self.plan_box.setEnabled(False)
        self._creating = True
        try:
            data = background_request(lambda: self.api.create_order(self.service.device_id, plan_id))
            if self._closed:
                return
            self._show_qrcode(data["qrcode"])
        except PaymentError as exc:
            if not self._closed:
                self.buy_btn.setEnabled(True)
                self.plan_box.setEnabled(True)
                info(self, "创建订单失败", str(exc))
            return
        finally:
            self._creating = False
        self.plan_box.setEnabled(False)
        self.status_label.setText("本地演示：不收款，约 6 秒后模拟成功。" if data.get("demo") else self.config.payment_hint)
        self.pay_thread = PayThread(self.api, data["orderId"], self.config, self)
        self.pay_thread.success.connect(self.on_pay_success)
        self.pay_thread.ended.connect(self._on_ended)
        self.pay_thread.network_issue.connect(self._on_network_issue)
        self.pay_thread.start()

    def _show_qrcode(self, encoded):
        try:
            raw = base64.b64decode(encoded.split(",", 1)[-1], validate=True)
            pix = QPixmap()
            if not pix.loadFromData(raw):
                raise ValueError("无效图片")
            self.qr_label.setPixmap(pix.scaled(240, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        except (ValueError, TypeError) as exc:
            self.qr_label.clear()
            raise PaymentError("订单已创建，但二维码图片无法显示。请核实订单后再重试。") from exc

    def _stop_thread(self):
        thread, self.pay_thread = self.pay_thread, None
        if thread is not None:
            thread.stop()
            # 不能像原代码那样等 1 秒后丢弃仍在网络请求中的 QThread。
            # Event 可立即中止轮询间隔；在途请求需等它返回或触发请求超时。
            wait_for_thread(thread)
            thread.deleteLater()

    def _on_network_issue(self, message):
        if self.sender() is self.pay_thread:
            self.status_label.setText(message)

    def _on_ended(self, message):
        if self.sender() is not self.pay_thread:
            return
        self._stop_thread()
        self.qr_label.clear()
        self.plan_box.setEnabled(True)
        self.buy_btn.setEnabled(True)
        self.status_label.setText(message)

    def on_pay_success(self):
        if self.sender() is not self.pay_thread:
            return
        self._stop_thread()
        self.qr_label.clear()
        self.plan_box.setEnabled(True)
        self.buy_btn.setEnabled(True)
        valid = self._refresh_license_label()
        if self._closed:
            return
        self.license_updated.emit()
        if valid and self.service.latest_license.get("isPaid") is True:
            info(self, "成功", "演示授权已生效，不涉及真实付款" if self.service.latest_license.get("demo") else self.config.paid_message)
        else:
            info(self, "付款已完成", "已检测到付款，授权暂未确认。请稍后刷新授权，勿重复付款。")

    def done(self, result):
        self._closed = True
        self._stop_thread()
        super().done(result)

    def closeEvent(self, event):
        self._closed = True
        self._stop_thread()
        super().closeEvent(event)


class LicenseController:
    def __init__(self, window, config, *, device_id=None, api=None, status_label=None):
        self.window = window
        self.service = LicenseService(api if api is not None else PaymentAPI(config), config, device_id)
        self.device_id = self.service.device_id
        self.status_label = status_label if status_label is not None else getattr(window, "label_license_status", None)
        self._checking = False
        self._gate_busy = False
        self._dialog_open = False

    @property
    def latest_license(self):
        return self.service.latest_license

    def refresh_license(self):
        if self._checking:
            return False
        self._checking = True
        try:
            valid = background_request(self.service.refresh_license)
        finally:
            self._checking = False
        self.device_id = self.service.device_id
        if self.status_label is not None:
            data = self.latest_license
            if valid:
                suffix = "（网络异常，暂用已验证状态）" if data.get("cached") else ""
                state = "试用中" if data.get("status") == "trial" else "已授权"
                text = f"授权状态：{state}（到期：{format_expiry(data.get('expireTime'))}）{suffix}"
            elif data.get("error"):
                text = "授权状态：暂时无法验证"
            else:
                text = "授权状态：设备已禁用，请联系客服" if data.get("status") == "disabled" else "授权状态：未授权"
            if data.get("demo"):
                text = "本地演示，不收款。" + text
            self.status_label.setText(text)
        return valid

    def open_buy_dialog(self):
        if self._dialog_open:
            return
        self._dialog_open = True
        try:
            self._open_buy_dialog()
        finally:
            self._dialog_open = False

    def _open_buy_dialog(self):
        dlg = BuyDialog(self.window, self.service)
        dlg.license_updated.connect(self.refresh_license)
        try:
            dlg.exec_()
        finally:
            dlg._stop_thread()
            dlg.deleteLater()
        self.refresh_license()

    def ensure_authorized(self, feature_name="此功能"):
        if self._gate_busy or self._checking or self._dialog_open:
            return False
        self._gate_busy = True
        try:
            return self._ensure_authorized(feature_name)
        finally:
            self._gate_busy = False

    def _ensure_authorized(self, feature_name):
        if self.refresh_license():
            return True
        if self.latest_license.get("error"):
            info(self.window, "授权验证失败", self.latest_license.get("message", "请检查网络后重试"))
            return False
        if self.latest_license.get("status") == "disabled":
            info(self.window, "设备已禁用", "请联系客服核实设备授权。")
            return False
        if ask_yes_no(self.window, "需要授权", f"使用「{feature_name}」需要授权。\n是否现在去购买授权？"):
            self.open_buy_dialog()
            return self.latest_license.get("valid") is True
        return False
