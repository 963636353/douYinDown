import threading
from datetime import datetime

from PyQt5.QtCore import QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    FluentIcon as FIF,
    FluentWindow,
    MessageBoxBase,
    PushButton,
    Theme,
    setTheme,
    setThemeColor,
)

from core.paths import ensure_runtime_dirs, runtime_path
from core.update_checker import fetch_update_info, has_new_version
from gui.download_tab import DownloadTab
from gui.login_tab import LoginTab
from gui.settings_tab import SettingsTab


_TITLE_BAR_HEIGHT = 48
_NAVIGATION_WIDTH = 110


class UpdateDialog(MessageBoxBase):
    """Fluent-style update prompt with download choices."""

    def __init__(self, update_info, parent=None):
        super().__init__(parent)
        self.update_info = update_info
        self.setClosableOnMaskClicked(not update_info.forced_upgrade)
        self._build_ui()

    def _build_ui(self):
        message = BodyLabel(self.update_info.info, self)
        message.setWordWrap(True)
        self.viewLayout.addWidget(message)

        self.yesButton.setText('百度网盘下载')
        self.yesButton.clicked.disconnect()
        self.yesButton.clicked.connect(lambda: self._open_download(self.update_info.baidu_url))

        self.cancelButton.setText('夸克网盘下载')
        self.cancelButton.clicked.disconnect()
        self.cancelButton.clicked.connect(lambda: self._open_download(self.update_info.quark_url))

        self.actionButton = PushButton(self.buttonGroup)
        if self.update_info.forced_upgrade:
            self.actionButton.setText('退出软件')
            self.actionButton.clicked.connect(QApplication.quit)
        else:
            self.actionButton.setText('暂不升级')
            self.actionButton.clicked.connect(self.reject)
        self.buttonLayout.addWidget(self.actionButton, 1)

        self.widget.setMinimumWidth(420)

    def _open_download(self, url):
        if url:
            QDesktopServices.openUrl(QUrl(url))
        self.accept()

    def reject(self):
        if self.update_info.forced_upgrade:
            return
        super().reject()


class UpdateCheckFailedDialog(MessageBoxBase):
    """Blocking update-check failure prompt."""

    def __init__(self, reason, parent=None):
        super().__init__(parent)
        self.reason = reason
        self.setClosableOnMaskClicked(False)
        self._build_ui()

    def _build_ui(self):
        message = BodyLabel(f'检查更新失败：{self.reason}', self)
        message.setWordWrap(True)
        self.viewLayout.addWidget(message)

        self.yesButton.setText('联系软件窗口顶部标题的客服')
        self.yesButton.clicked.disconnect()
        self.yesButton.clicked.connect(self._quit_application)
        self.hideCancelButton()

        self.widget.setMinimumWidth(460)

    def _quit_application(self):
        self.accept()
        QApplication.quit()

    def reject(self):
        return


class MainWindow(FluentWindow):
    """Main application window with QFluentWidgets left navigation."""

    update_available = pyqtSignal(object)
    update_status = pyqtSignal(str)
    update_check_failed = pyqtSignal(str)

    def __init__(self, platforms):
        super().__init__()
        self.platforms = platforms
        self.update_available.connect(self._show_update_dialog)
        self.update_status.connect(self._status_log)
        self.update_check_failed.connect(self._show_update_check_failed_dialog)
        setTheme(Theme.AUTO)
        setThemeColor('#0078D4')

        self.setWindowTitle('青柠视频下载 ——作者微信z851968（软件相关的问题可直接添加咨询，添加时注明来意）')
        self.resize(980, 720)
        self.setMinimumSize(820, 560)

        self._configure_content_area()
        self._configure_navigation()
        self._build_status_bar()
        self._build_ui()
        self._sync_title_bar()
        QTimer.singleShot(500, self._start_update_check)

    def _configure_content_area(self):
        self.hBoxLayout.setContentsMargins(0, _TITLE_BAR_HEIGHT, 0, 0)
        self.widgetLayout.setContentsMargins(0, 0, 0, 0)

    def _configure_navigation(self):
        self.navigationInterface.setExpandWidth(_NAVIGATION_WIDTH)
        self.navigationInterface.setMinimumExpandWidth(0)
        self.navigationInterface.setMenuButtonVisible(False)
        self.navigationInterface.setReturnButtonVisible(False)
        self.navigationInterface.setCollapsible(False)
        self.navigationInterface.expand(False)
        self.navigationInterface.setFixedWidth(_NAVIGATION_WIDTH)

    def _sync_title_bar(self):
        self.titleBar.move(0, 0)
        self.titleBar.resize(self.width(), _TITLE_BAR_HEIGHT)

    def _build_status_bar(self):
        self.content_wrapper = QWidget(self)
        wrapper_layout = QVBoxLayout(self.content_wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(0)

        self.widgetLayout.removeWidget(self.stackedWidget)
        wrapper_layout.addWidget(self.stackedWidget, 1)

        status_bar = QWidget(self.content_wrapper)
        status_bar.setObjectName('statusBar')
        status_layout = QHBoxLayout(status_bar)
        status_layout.setContentsMargins(16, 6, 16, 8)
        status_layout.setSpacing(0)
        self.status_label = BodyLabel('就绪', status_bar)
        status_layout.addWidget(self.status_label)
        wrapper_layout.addWidget(status_bar)

        self.widgetLayout.addWidget(self.content_wrapper)
        self.content_wrapper.setStyleSheet(
            '#statusBar { border-top: 1px solid rgba(0, 0, 0, 0.08); }'
        )

    def _build_ui(self):
        platform = self.platforms[0] if self.platforms else None

        self.settings_tab = SettingsTab(self, status_callback=self._status_log)
        self.login_tab = LoginTab(
            self,
            platform=platform,
            status_callback=self._status_log,
        )
        self.download_tab = DownloadTab(
            self,
            platforms=self.platforms,
            status_callback=self._status_log,
            settings_getter=self.settings_tab.get_config,
        )

        self.addSubInterface(self.download_tab, FIF.DOWNLOAD, '下载')
        self.addSubInterface(self.login_tab, FIF.FINGERPRINT, '登录')
        self.addSubInterface(self.settings_tab, FIF.SETTING, '设置')

    @pyqtSlot(str)
    def _status_log(self, message):
        self.status_label.setText(message[:120])
        try:
            print(message)
        except Exception:
            pass
        try:
            ensure_runtime_dirs()
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            log_message = message.replace('\r', '').rstrip()
            with open(runtime_path('logs', 'app.log'), 'a', encoding='utf-8') as log_file:
                log_file.write(f'[{timestamp}] {log_message}\n')
        except Exception:
            pass

    def _start_update_check(self):
        self.update_status.emit('[*] 正在检查软件更新...')

        def _worker():
            try:
                update_info = fetch_update_info()
                if has_new_version(update_info):
                    self.update_available.emit(update_info)
                else:
                    self.update_status.emit('[OK] 当前已是最新版本')
            except Exception as exc:
                self.update_check_failed.emit(str(exc))

        threading.Thread(target=_worker, daemon=True).start()

    @pyqtSlot(object)
    def _show_update_dialog(self, update_info):
        dialog = UpdateDialog(update_info, self)
        dialog.exec()
        if update_info.forced_upgrade:
            QApplication.quit()

    @pyqtSlot(str)
    def _show_update_check_failed_dialog(self, reason):
        dialog = UpdateCheckFailedDialog(reason, self)
        dialog.exec()
        QApplication.quit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_title_bar()
