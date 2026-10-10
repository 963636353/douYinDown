"""到期提示、套餐选择与扫码支付共用一个窗口。"""

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QButtonGroup, QGridLayout, QLabel, QWidget
from qfluentwidgets import BodyLabel, MessageBoxBase, PrimaryPushButton, PushButton, StrongBodyLabel, TogglePushButton

from payment_kit import PaymentError
from payment_kit.qt import BuyDialog, PayThread, background_request, format_plan_name
from gui.about_tab import AUTHOR_WECHAT


class AuthorizationDialog(MessageBoxBase):
    def __init__(self, parent, service, feature_name):
        super().__init__(parent)
        self.service = service
        self.api, self.config = service.api, service.config
        self.feature_name = feature_name
        self.pay_thread = None
        self._order = None
        self._paid_seen = False
        self._closed = False
        self._busy = False
        self._finishing = False
        self._plans = {}
        self.title = StrongBodyLabel("使用期限已到期", self)
        self.hint = BodyLabel("请选择套餐继续使用，付款后会自动继续本次下载。", self)
        self.hint.setWordWrap(True)
        self.plan_widget = QWidget(self)
        self.plan_layout = QGridLayout(self.plan_widget)
        self.plan_layout.setContentsMargins(0, 0, 0, 0)
        self.plan_layout.setSpacing(10)
        self.plan_group = QButtonGroup(self)
        self.plan_group.setExclusive(True)
        self.buy_button = PrimaryPushButton("立即支付", self)
        self.buy_button.clicked.connect(self.create_order)
        self.order_label = BodyLabel("", self)
        self.qr_label = QLabel(self)
        self.qr_label.setAlignment(Qt.AlignCenter)
        self.qr_label.setFixedHeight(200)
        self.status_label = BodyLabel("正在加载套餐…", self)
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumHeight(36)
        self.contact = BodyLabel(f"支付或授权异常，请联系作者微信：{AUTHOR_WECHAT}。", self)
        self.contact.setWordWrap(True)
        self.viewLayout.setContentsMargins(20, 16, 20, 16)
        self.viewLayout.setSpacing(8)
        for widget in (self.title, self.hint, self.plan_widget, self.buy_button,
                       self.order_label, self.qr_label, self.status_label, self.contact):
            self.viewLayout.addWidget(widget)
        self.order_label.hide()
        self.qr_label.hide()
        self.yesButton.setText("刷新状态")
        self.yesButton.clicked.disconnect()
        self.yesButton.clicked.connect(self.refresh_status)
        self.change_button = PushButton("更换套餐", self.buttonGroup)
        self.change_button.clicked.connect(self.change_plan)
        self.change_button.hide()
        self.buttonLayout.insertWidget(1, self.change_button, 1)
        self.cancelButton.setText("关闭")
        self.widget.setMinimumWidth(560)
        self.finish_timer = QTimer(self)
        self.finish_timer.setSingleShot(True)
        self.finish_timer.timeout.connect(self._continue_download)
        self._sync_controls()
        QTimer.singleShot(0, self.load_plans)

    def _sync_controls(self):
        ready = not (self._closed or self._busy or self._finishing)
        data = self.service.latest_license
        can_buy = data.get("status") == "expired" and not data.get("error")
        self.buy_button.setEnabled(ready and can_buy and bool(self._plans) and self._order is None)
        self.plan_widget.setEnabled(ready and self._order is None)
        self.yesButton.setEnabled(ready)
        self.change_button.setEnabled(ready and self._order is not None and not self._paid_seen)

    def _set_busy(self, value):
        self._busy = value
        self._sync_controls()

    def load_plans(self):
        if self._closed or self._busy or self._finishing:
            return
        self._set_busy(True)
        try:
            plans = background_request(self.api.get_plans)
            if self._closed:
                return
            for button in self.plan_group.buttons():
                self.plan_group.removeButton(button)
                self.plan_layout.removeWidget(button)
                button.deleteLater()
            self._plans.clear()
            for index, plan in enumerate(plans):
                name = format_plan_name(plan)
                button = TogglePushButton(f"{name}\n￥{plan.get('price', '?')}", self.plan_widget)
                button.setMinimumHeight(70)
                self.plan_group.addButton(button, index)
                self.plan_layout.addWidget(button, index // 3, index % 3)
                self._plans[index] = plan
            if plans:
                self.plan_group.button(0).setChecked(True)
                self.status_label.setText("选择套餐后点击“立即支付”。")
            else:
                self.status_label.setText("暂无可购买的套餐，请稍后刷新状态或联系客服。")
        except PaymentError as exc:
            if not self._closed:
                self.status_label.setText(f"获取套餐失败：{exc}。请点击“刷新状态”重试。")
        finally:
            self._set_busy(False)

    def create_order(self):
        if not self.buy_button.isEnabled() or self._order is not None:
            return
        plan = self._plans.get(self.plan_group.checkedId())
        if plan is None:
            self.status_label.setText("请先选择套餐。")
            return
        self._set_busy(True)
        self.status_label.setText("正在生成支付二维码…")
        try:
            data = background_request(lambda: self.api.create_order(self.service.device_id, plan["planId"]))
            if self._closed:
                return
            self._order = data
            selected = self.plan_group.checkedButton().text().replace("\n", " · ")
            self.order_label.setText("已选套餐：" + selected)
            self.order_label.show()
            self.hint.hide()
            self.plan_widget.hide()
            self.buy_button.hide()
            self.change_button.show()
            # 沿用已经验证过的二维码解析逻辑，不创建第二个购买窗口。
            try:
                BuyDialog._show_qrcode(self, data["qrcode"])
                self.qr_label.setPixmap(self.qr_label.pixmap().scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self.qr_label.show()
                self.status_label.setText("本地演示：不收款，约 6 秒后模拟成功。" if data.get("demo")
                                          else "请使用微信扫码支付，付款后自动继续下载。")
            except PaymentError as exc:
                self.status_label.setText(str(exc))
            self._start_polling()
        except PaymentError as exc:
            if not self._closed:
                self.status_label.setText(f"生成支付二维码失败：{exc}。若已付款，请先刷新状态。")
        finally:
            self._set_busy(False)

    def _start_polling(self):
        if self._closed or self._finishing or self.pay_thread is not None or self._order is None or self._paid_seen:
            return
        thread = PayThread(self.api, self._order["orderId"], self.config, self)
        thread.success.connect(self.on_pay_success)
        thread.ended.connect(self._on_ended)
        thread.network_issue.connect(self._on_network_issue)
        self.pay_thread = thread
        thread.start()

    # 共享安全停止逻辑：关闭窗口时保留服务器订单，等待在途请求结束。
    _stop_thread = BuyDialog._stop_thread

    def _on_network_issue(self, message):
        if not self._closed and not self._finishing and self.pay_thread is not None and self.sender() is self.pay_thread:
            self.status_label.setText(message)

    def _on_ended(self, message):
        if self._closed or self._finishing or self.pay_thread is None or self.sender() is not self.pay_thread:
            return
        self._stop_thread()
        self.status_label.setText(message.replace("请关闭后刷新授权", "请点击“刷新状态”"))
        self._sync_controls()

    def on_pay_success(self):
        if self._closed or self._finishing or self.pay_thread is None or self.sender() is not self.pay_thread:
            return
        self._paid_seen = True
        self._stop_thread()
        self.refresh_status()

    def refresh_status(self):
        if self._closed or self._busy or self._finishing:
            return
        self._set_busy(True)
        try:
            valid = background_request(self.service.refresh_license)
            if self._closed:
                return
            data = self.service.latest_license
            if valid and data.get("isPaid") is True and not data.get("error") and not data.get("cached"):
                self._finishing = True
                self._stop_thread()
                if self._closed:
                    return
                self.title.setText("授权已开通")
                self.qr_label.clear()
                self.qr_label.hide()
                self.plan_widget.hide()
                self.buy_button.hide()
                self.hint.hide()
                self.status_label.setText("演示授权已生效，正在继续下载…" if data.get("demo") else "支付成功，正在继续下载…")
                self.finish_timer.start(700)
            elif data.get("error"):
                self.status_label.setText(str(data.get("message") or "暂时无法验证使用状态，请稍后刷新。"))
            elif data.get("status") == "disabled":
                self._stop_thread()
                self.status_label.setText("设备已禁用，请联系客服核实。")
            elif self._paid_seen:
                self.status_label.setText("已检测到付款，授权暂未确认。请刷新状态，勿重复付款。")
            elif self._order is not None:
                self.status_label.setText("尚未确认付款，请使用微信扫码；若已付款请稍后刷新状态。")
                self._start_polling()
            else:
                self.status_label.setText("请选择套餐继续使用。")
        finally:
            self._set_busy(False)
        if not self._closed and not self._finishing and not self._plans:
            self.load_plans()

    def change_plan(self):
        if not self.change_button.isEnabled():
            return
        self.refresh_status()
        data = self.service.latest_license
        if self._closed or self._finishing or self._paid_seen or data.get("error") or data.get("status") != "expired":
            return
        self._stop_thread()
        if self._closed:
            return
        self._order = None
        self.qr_label.clear()
        self.qr_label.hide()
        self.order_label.hide()
        self.change_button.hide()
        self.hint.show()
        self.plan_widget.show()
        self.buy_button.show()
        self.status_label.setText("请选择套餐。更换套餐不会撤销原订单；若已付款请先刷新状态。")
        self._sync_controls()

    def _continue_download(self):
        if self._closed or self._busy:
            return
        self._set_busy(True)
        try:
            # 自动继续前再确认一次，网络失败或期限再次到期则仍留在此窗口。
            valid = background_request(self.service.refresh_license)
            if self._closed:
                return
            data = self.service.latest_license
            if valid and data.get("isPaid") is True and not data.get("error") and not data.get("cached"):
                self.accept()
            else:
                self._finishing = False
                self.status_label.setText(str(data.get("message") or "暂时无法确认有效授权，请刷新状态或联系客服。"))
        finally:
            self._set_busy(False)

    def done(self, result):
        self._closed = True
        self.finish_timer.stop()
        self._stop_thread()
        super().done(result)

    def closeEvent(self, event):
        self._closed = True
        self.finish_timer.stop()
        self._stop_thread()
        super().closeEvent(event)
