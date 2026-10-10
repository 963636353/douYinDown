import threading

from PyQt5.QtCore import Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    FluentIcon as FIF,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
)

from core.browser_login import perform_browser_login
from core.cookie_manager import clear_cookies, load_cookies


class LoginTab(QWidget):
    """Page for managing platform login and cookies."""

    status_message = pyqtSignal(str)
    login_done = pyqtSignal(object)

    def __init__(self, parent, platform, status_callback):
        super().__init__(parent)
        self.setObjectName('loginInterface')
        self.platform = platform
        self.status_callback = status_callback
        self._cookies = None
        self._last_login_message = ''

        self.status_message.connect(status_callback)
        self.login_done.connect(self._login_done)

        self._build_ui()
        self._refresh_status()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 18)
        root.setSpacing(14)

        status_card = CardWidget(self)
        status_layout = QGridLayout(status_card)
        status_layout.setContentsMargins(18, 16, 18, 18)
        status_layout.setHorizontalSpacing(12)
        status_layout.setVerticalSpacing(12)

        self.status_value = StrongBodyLabel('未知', status_card)
        self.cookie_path_value = BodyLabel(self._cookie_path(), status_card)
        status_layout.addWidget(BodyLabel('状态:', status_card), 0, 0, Qt.AlignRight)
        status_layout.addWidget(self.status_value, 0, 1)
        status_layout.addWidget(BodyLabel('Cookie 文件:', status_card), 1, 0, Qt.AlignRight)
        status_layout.addWidget(self.cookie_path_value, 1, 1)
        status_layout.setColumnStretch(1, 1)
        root.addWidget(status_card)

        action_card = CardWidget(self)
        action_layout = QHBoxLayout(action_card)
        action_layout.setContentsMargins(18, 16, 18, 18)
        action_layout.setSpacing(10)

        self.login_btn = PrimaryPushButton(FIF.FINGERPRINT, '浏览器扫码登录', action_card)
        self.login_btn.clicked.connect(self._start_login)
        self.clear_btn = PushButton(FIF.DELETE, '清除 Cookie', action_card)
        self.clear_btn.clicked.connect(self._do_clear_cookies)
        self.verify_btn = PushButton(FIF.SYNC, '检查登录信息', action_card)
        self.verify_btn.clicked.connect(self._refresh_status)
        action_layout.addWidget(self.login_btn)
        action_layout.addWidget(self.clear_btn)
        action_layout.addWidget(self.verify_btn)
        action_layout.addStretch(1)
        root.addWidget(action_card)

        hint_card = CardWidget(self)
        hint_layout = QVBoxLayout(hint_card)
        hint_layout.setContentsMargins(18, 16, 18, 18)
        hint_layout.setSpacing(10)
        hint_layout.addWidget(StrongBodyLabel('使用说明', hint_card))
        hints = (
            '1. 点击“浏览器扫码登录”按钮\n'
            '2. 浏览器窗口将自动打开并跳转到抖音登录页\n'
            '3. 使用抖音 App 扫描二维码\n'
            '4. 登录成功后 Cookie 将自动保存，窗口自动关闭\n\n'
            '提示: 首次使用需要安装 Chrome 或 Edge 浏览器'
        )
        hint_text = BodyLabel(hints, hint_card)
        hint_text.setWordWrap(True)
        hint_layout.addWidget(hint_text)
        self.chrome_download_btn = PushButton(FIF.LINK, '下载 Chrome 浏览器', hint_card)
        self.chrome_download_btn.clicked.connect(self._open_chrome_download)
        hint_layout.addWidget(self.chrome_download_btn, 0, Qt.AlignLeft)
        root.addWidget(hint_card)
        root.addStretch(1)

    def _cookie_path(self):
        if not self.platform:
            return '未配置平台'
        return f'cookies/{self.platform.name}_cookies.txt'

    def _set_status(self, text, color):
        self.status_value.setText(text)
        self.status_value.setStyleSheet(f'color: {color};')

    def _refresh_status(self):
        if not self.platform:
            self._cookies = None
            self._set_status('无可用平台', '#C42B1C')
            self.status_message.emit('[!] 没有可用的平台')
            return

        cookies = load_cookies(self.platform.name)
        if cookies and any(cookies.get(k) for k in self.platform.session_cookie_names):
            self._cookies = cookies
            self._set_status('已保存登录信息', '#107C10')
            self.status_message.emit('[OK] 本地登录 Cookie 存在且未过期；实际访问结果以抖音接口为准')
        else:
            self._cookies = None
            self._set_status('未登录', '#C42B1C')
            self.status_message.emit('[!] 登录 Cookie 缺失或已过期，请重新扫码登录')

    def _open_chrome_download(self):
        url = 'https://www.google.cn/chrome/index.html'
        if QDesktopServices.openUrl(QUrl(url)):
            self.status_message.emit('[OK] 已打开 Chrome 下载页面')
            return

        self._show_message('错误', f'无法打开 Chrome 下载页面，请手动访问 {url}')

    def _start_login(self):
        if not self.platform:
            self._show_message('错误', '没有可用的平台')
            return

        self.login_btn.setEnabled(False)
        self.login_btn.setText('登录中...')
        self._set_status('正在打开浏览器...', '#005FB8')

        def _login_thread():
            last_message = {'text': ''}

            def _emit_status(message):
                message = str(message)
                cleaned = message.replace('\r', '').strip()
                if cleaned:
                    last_message['text'] = cleaned
                self.status_message.emit(message)

            try:
                result = perform_browser_login(self.platform, _emit_status)
            except Exception as exc:
                result = None
                _emit_status(f'[!] 登录线程异常: {exc}')
            self.login_done.emit((result, last_message['text']))

        threading.Thread(target=_login_thread, daemon=True).start()

    def _login_done(self, payload):
        last_message = ''
        result = payload
        if isinstance(payload, tuple) and len(payload) == 2:
            result, last_message = payload
        self._last_login_message = last_message

        self.login_btn.setEnabled(True)
        self.login_btn.setText('浏览器扫码登录')
        if result:
            self._cookies = result
            self._set_status('已登录', '#107C10')
            self.status_message.emit(f'[OK] 登录成功，已获取 {len(result)} 个 cookie')
            self._show_message('登录成功', f'已获取 {len(result)} 个 cookie')
        else:
            self._set_status('登录失败', '#C42B1C')
            self.status_message.emit('[!] 登录失败')
            detail = last_message or '浏览器未能启动或未取得登录 Cookie'
            self._show_message(
                '登录失败',
                f'{detail}\n\n请确认已安装 Chrome 或 Edge；详细日志见 logs/app.log。',
            )

    def _do_clear_cookies(self):
        if not self.platform:
            self._show_message('错误', '没有可用的平台')
            return

        dialog = MessageBox('确认', '确定要清除 Cookie 吗？需要重新登录才能使用。', self.window())
        dialog.yesButton.setText('确定')
        dialog.cancelButton.setText('取消')
        if not dialog.exec():
            return

        clear_cookies(self.platform.name)
        self._refresh_status()
        self.status_message.emit('[OK] Cookie 已清除')

    def _show_message(self, title, content):
        dialog = MessageBox(title, content, self.window())
        dialog.hideCancelButton()
        dialog.yesButton.setText('知道了')
        dialog.exec()

    def get_cookies(self):
        if not self._cookies:
            self._refresh_status()
        return self._cookies
