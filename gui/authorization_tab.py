from PyQt5.QtCore import QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import QApplication, QDialog, QWidget
from qfluentwidgets import BodyLabel

from core.payment_config import client_data_dir, credentials_path, legacy_credentials_path, load_payment_config, service_key
from payment_kit import PaymentAPI, PaymentError
from payment_kit.computer import ComputerIdentity
from payment_kit.dialogs import info
from payment_kit.identity import DeviceIdentity
from payment_kit.qt import LicenseController, background_request
from payment_kit.usage import UsageReporter
from gui.payment_dialog import AuthorizationDialog


class AuthorizationTab(QWidget):
    """常驻后台的设备登记、统计与使用权限检查，不加入导航。"""

    computer_uuid_changed = pyqtSignal(str)

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("authorizationInterface")
        self.controller = None
        self.computer_uuid = ''
        self.computer_uuid_changed.connect(self._remember_computer_uuid)
        self._busy = False
        self._gate_busy = False
        self._closing = False
        self._authorization_dialog = None
        self._configuration_error = ""
        self._config = None
        self._reporter = None
        self.label_license_status = BodyLabel("授权状态：等待验证", self)
        try:
            self._config = load_payment_config()
        except (ValueError, OSError, TypeError) as exc:
            self._configuration_error = str(exc)
            self.label_license_status.setText("授权状态：配置异常，请联系客服")
        QApplication.instance().aboutToQuit.connect(self._on_quit)
        if self._config:
            QTimer.singleShot(0, self.refresh_license)

    def _create_client(self):
        config = self._config
        computer = ComputerIdentity(client_data_dir() / "computer.json")
        # 本机身份读取完成即可展示，不依赖联网登记或授权查询成功。
        self.computer_uuid_changed.emit(computer.uuid)
        identity = DeviceIdentity(credentials_path(config.api_base), config.api_base,
                                  device_id=computer.uuid, legacy_path=legacy_credentials_path(config.api_base))
        api = PaymentAPI(config, identity)
        api.startup()
        reporter = UsageReporter(api, computer.uuid, client_data_dir() / "usage" / (service_key(config.api_base) + ".db"))
        return identity, api, reporter

    @pyqtSlot(str)
    def _remember_computer_uuid(self, computer_uuid):
        self.computer_uuid = computer_uuid

    def _initialize_client(self):
        if not self._config:
            return False
        self._set_busy(True)
        self.label_license_status.setText("授权状态：正在验证")
        try:
            identity, api, reporter = background_request(self._create_client)
            if self._closing:
                reporter.close()
                return False
            self.computer_uuid_changed.emit(identity.computer_uuid)
            self.controller = LicenseController(self.window(), self._config, device_id=identity.device_id,
                                                api=api, status_label=self.label_license_status)
            self._reporter = reporter
            reporter.start()
        except (ValueError, OSError, TypeError, PaymentError) as exc:
            if not self.computer_uuid:
                self.computer_uuid_changed.emit('')
            self._configuration_error = str(exc)
            message = str(exc) if isinstance(exc, (PaymentError, ValueError)) else "本机使用凭据异常，请联系客服"
            self.label_license_status.setText("授权状态：" + message)
            return False
        finally:
            self._set_busy(False)
        return self.refresh_license()

    def record_video_download(self):
        # 下载线程调用，只操作持久化队列，不读写 Qt 控件。
        if self._reporter is not None:
            self._reporter.record_video()

    def _on_quit(self):
        self._closing = True
        if self._authorization_dialog is not None:
            self._authorization_dialog.reject()
        if self._reporter is not None:
            self._reporter.close()

    def _set_busy(self, busy):
        self._busy = busy

    def _configured(self):
        if self.controller is not None:
            return True
        self._initialize_client()
        return self.controller is not None

    def refresh_license(self):
        if self._busy or self._closing:
            return False
        if self.controller is None:
            return self._initialize_client()
        self._set_busy(True)
        try:
            return self.controller.refresh_license()
        finally:
            self._set_busy(False)

    def ensure_authorized(self, feature_name):
        if self._busy or self._closing or self._gate_busy:
            return False
        self._gate_busy = True
        try:
            if not self._configured():
                self._show_check_error(self._configuration_error)
                return False
            if self.refresh_license():
                return True
            data = self.controller.latest_license
            if data.get("error"):
                self._show_check_error(data.get("message"))
                return False
            if data.get("status") == "disabled":
                info(self.window(), "设备已禁用", "当前设备已禁用，请联系客服核实。")
                return False
            if data.get("status") != "expired":
                self._show_check_error("暂时无法确认使用状态，请稍后重试或联系客服。")
                return False
            return self._open_authorization_dialog(feature_name) and not self._closing
        finally:
            self._gate_busy = False

    def _show_check_error(self, message):
        message = message or "暂时无法连接服务，请检查网络后重试或联系客服。"
        info(self.window(), "暂时无法验证使用状态", str(message).replace("支付/授权服务", "服务"))

    def _open_authorization_dialog(self, feature_name):
        dialog = AuthorizationDialog(self.window(), self.controller.service, feature_name)
        self._authorization_dialog = dialog
        try:
            return (dialog.exec_() == QDialog.Accepted
                    and self.controller.latest_license.get("valid") is True
                    and self.controller.latest_license.get("isPaid") is True
                    and not self.controller.latest_license.get("error"))
        finally:
            dialog._stop_thread()
            self._authorization_dialog = None
            dialog.deleteLater()
