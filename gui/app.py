import threading
from datetime import datetime

from PyQt5.QtCore import Qt, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    FluentIcon as FIF,
    FluentWindow,
    MessageBoxBase,
    PushButton,
    StrongBodyLabel,
    TextBrowser,
    Theme,
    setTheme,
    setThemeColor,
)

from core.paths import ensure_runtime_dirs, runtime_path
from core.update_checker import fetch_update_info, has_new_version
from gui.about_tab import AboutTab, AUTHOR_WECHAT
from gui.authorization_tab import AuthorizationTab
from gui.download_tab import DownloadTab
from gui.feedback_tab import FeedbackTab
from gui.login_tab import LoginTab
from gui.settings_tab import SettingsTab


_TITLE_BAR_HEIGHT = 48
_NAVIGATION_WIDTH = 132


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
        message = BodyLabel(f'检查更新失败：{self.reason}\n请联系作者微信：{AUTHOR_WECHAT}', self)
        message.setWordWrap(True)
        self.viewLayout.addWidget(message)

        self.yesButton.setText('退出软件')
        self.yesButton.clicked.disconnect()
        self.yesButton.clicked.connect(self._quit_application)
        self.hideCancelButton()

        self.widget.setMinimumWidth(460)

    def _quit_application(self):
        self.accept()
        QApplication.quit()

    def reject(self):
        return


class AnnouncementDialog(MessageBoxBase):
    """每次启动的纯文本公告，长内容在窗口内滚动。"""

    def __init__(self, announcement, parent):
        super().__init__(parent)
        self.setWindowTitle('软件公告')
        self.setClosableOnMaskClicked(True)
        self.title = StrongBodyLabel('软件公告', self)
        self.content = TextBrowser(self)
        self.content.setPlainText(announcement)
        self.content.setOpenLinks(False)
        self.content.setOpenExternalLinks(False)
        self.content.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.content.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        width = min(560, max(280, parent.width() - 48))
        self.widget.setFixedWidth(width)
        self.content.document().setTextWidth(width - 72)
        height = int(self.content.document().size().height()) + 20
        max_height = min(320, max(80, parent.height() - 220))
        self.content.setFixedHeight(min(max_height, max(80, height)))
        self.viewLayout.addWidget(self.title)
        self.viewLayout.addWidget(self.content)
        self.yesButton.setText('我知道了')
        self.hideCancelButton()


class MainWindow(FluentWindow):
    """Main application window with QFluentWidgets left navigation."""

    update_checked = pyqtSignal(object)
    update_status = pyqtSignal(str)
    update_check_failed = pyqtSignal(str)

    def __init__(self, platforms):
        super().__init__()
        self.platforms = platforms
        self._startup_notices_handled = False
        self._closing = False
        self.update_checked.connect(self._handle_update_info)
        self.update_status.connect(self._status_log)
        self.update_check_failed.connect(self._show_update_check_failed_dialog)
        setTheme(Theme.AUTO)
        setThemeColor('#0078D4')

        self.setWindowTitle('青柠视频下载')
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
        self.authorization_tab = AuthorizationTab(self)
        self.authorization_tab.hide()
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
            authorization_check=self.authorization_tab.ensure_authorized,
            download_success_callback=self.authorization_tab.record_video_download,
        )
        self.settings_tab.config_saved.connect(self.download_tab.apply_settings)
        self.about_tab = AboutTab(self)
        self.feedback_tab = FeedbackTab(self)
        self.authorization_tab.computer_uuid_changed.connect(self.about_tab.set_computer_uuid)
        if self.authorization_tab.computer_uuid:
            self.about_tab.set_computer_uuid(self.authorization_tab.computer_uuid)

        self.addSubInterface(self.download_tab, FIF.DOWNLOAD, '下载')
        self.addSubInterface(self.login_tab, FIF.FINGERPRINT, '登录')
        self.addSubInterface(self.settings_tab, FIF.SETTING, '设置')
        self.addSubInterface(self.about_tab, FIF.INFO, '说明')
        self.addSubInterface(self.feedback_tab, FIF.FEEDBACK, '问题反馈')

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
        if self._closing or self._startup_notices_handled:
            return
        self.update_status.emit('[*] 正在检查软件更新...')

        def _worker():
            try:
                update_info = fetch_update_info()
                if not self._closing:
                    self.update_checked.emit(update_info)
            except Exception as exc:
                if not self._closing:
                    self.update_check_failed.emit(str(exc))

        threading.Thread(target=_worker, daemon=True).start()

    @pyqtSlot(object)
    def _handle_update_info(self, update_info):
        if self._closing or self._startup_notices_handled:
            return
        # 在打开模态窗口前标记，重复信号或嵌套事件循环不会叠加公告。
        self._startup_notices_handled = True
        if has_new_version(update_info):
            self._show_update_dialog(update_info)
            if update_info.forced_upgrade:
                return
        else:
            self._status_log('[OK] 当前已是最新版本')
        if not self._closing and update_info.announcement:
            self._show_announcement(update_info.announcement)

    @pyqtSlot(object)
    def _show_update_dialog(self, update_info):
        dialog = UpdateDialog(update_info, self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()
        if update_info.forced_upgrade:
            QApplication.quit()

    def _show_announcement(self, announcement):
        dialog = AnnouncementDialog(announcement, self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    @pyqtSlot(str)
    def _show_update_check_failed_dialog(self, reason):
        if self._closing or self._startup_notices_handled:
            return
        self._startup_notices_handled = True
        dialog = UpdateCheckFailedDialog(reason, self)
        dialog.exec()
        QApplication.quit()

    def closeEvent(self, event):
        self._closing = True
        super().closeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_title_bar()
