from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CardWidget, PrimaryPushButton

from core import update_checker


AUTHOR_WECHAT = 'z851968'
TUTORIAL_URL = 'https://my.feishu.cn/wiki/ChJ6wACWVizkAskVSjbcxzHknUe?from=from_copylink'


class AboutTab(QWidget):
    """版本、作者联系方式、电脑身份和教程入口。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName('aboutInterface')
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 18)
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(18)
        self.version_label = BodyLabel(f'版本号：{update_checker.APP_VERSION}', card)
        self.author_label = BodyLabel(
            f'作者微信：{AUTHOR_WECHAT} (有问题可以直接和我沟通添加后说明来意)', card)
        self.author_label.setWordWrap(True)
        self.identity_label = BodyLabel('身份信息：正在读取…', card)
        self.identity_label.setWordWrap(True)
        for label in (self.version_label, self.author_label, self.identity_label):
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(label)
        self.tutorial_button = PrimaryPushButton('软件介绍和教程点我查看', card)
        self.tutorial_button.clicked.connect(self._open_tutorial)
        layout.addWidget(self.tutorial_button, 0, Qt.AlignLeft)
        root.addWidget(card)
        root.addStretch(1)

    def set_computer_uuid(self, computer_uuid):
        self.identity_label.setText(f'身份信息：{computer_uuid or "暂未获取"}')

    def _open_tutorial(self):
        QDesktopServices.openUrl(QUrl(TUTORIAL_URL))
