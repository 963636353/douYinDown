import json
import os

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QFileDialog, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CheckBox,
    FluentIcon as FIF,
    LineEdit,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    SpinBox,
)

from core.paths import runtime_path


CONFIG_PATH = runtime_path('config.json')

DEFAULT_CONFIG = {
    'download_dir': '',
    'proxy_enabled': False,
    'proxy_url': '',
    'max_concurrent': 2,
    'download_cover': False,
    'open_folder_after': False,
}


class SettingsTab(QWidget):
    """Page for application settings."""

    status_message = pyqtSignal(str)

    def __init__(self, parent, status_callback):
        super().__init__(parent)
        self.setObjectName('settingsInterface')
        self.status_callback = status_callback
        self.config = dict(DEFAULT_CONFIG)
        self.status_message.connect(status_callback)
        self._load_config()
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 18)
        root.setSpacing(14)

        download_card = CardWidget(self)
        download_layout = QGridLayout(download_card)
        download_layout.setContentsMargins(18, 16, 18, 18)
        download_layout.setHorizontalSpacing(12)
        download_layout.setVerticalSpacing(12)

        download_layout.addWidget(BodyLabel('保存目录:', download_card), 0, 0, Qt.AlignRight)
        self.dir_edit = LineEdit(download_card)
        self.dir_edit.setText(self.config.get('download_dir', ''))
        self.dir_edit.setPlaceholderText('为空时使用项目内 downloads 目录')
        if hasattr(self.dir_edit, 'setClearButtonEnabled'):
            self.dir_edit.setClearButtonEnabled(True)
        download_layout.addWidget(self.dir_edit, 0, 1)

        browse_btn = PushButton(FIF.FOLDER, '浏览...', download_card)
        browse_btn.clicked.connect(self._browse_dir)
        download_layout.addWidget(browse_btn, 0, 2)

        self.cover_check = CheckBox('下载封面图片', download_card)
        self.cover_check.setChecked(self.config.get('download_cover', False))
        download_layout.addWidget(self.cover_check, 1, 1, 1, 2)

        self.open_check = CheckBox('下载完成打开所在文件夹', download_card)
        self.open_check.setChecked(self.config.get('open_folder_after', False))
        download_layout.addWidget(self.open_check, 2, 1, 1, 2)

        download_layout.addWidget(BodyLabel('并发下载数:', download_card), 3, 0, Qt.AlignRight)
        self.concurrent_spin = SpinBox(download_card)
        self.concurrent_spin.setRange(1, 10)
        self.concurrent_spin.setValue(self.config.get('max_concurrent', 2))
        download_layout.addWidget(self.concurrent_spin, 3, 1, Qt.AlignLeft)
        download_layout.setColumnStretch(1, 1)
        root.addWidget(download_card)

        proxy_card = CardWidget(self)
        proxy_layout = QGridLayout(proxy_card)
        proxy_layout.setContentsMargins(18, 16, 18, 18)
        proxy_layout.setHorizontalSpacing(12)
        proxy_layout.setVerticalSpacing(12)

        self.proxy_enable_check = CheckBox('启用代理', proxy_card)
        self.proxy_enable_check.setChecked(self.config.get('proxy_enabled', False))
        self.proxy_enable_check.stateChanged.connect(self._toggle_proxy)
        proxy_layout.addWidget(self.proxy_enable_check, 0, 1)

        proxy_layout.addWidget(BodyLabel('代理地址:', proxy_card), 1, 0, Qt.AlignRight)
        self.proxy_entry = LineEdit(proxy_card)
        self.proxy_entry.setText(self.config.get('proxy_url', ''))
        self.proxy_entry.setPlaceholderText('例如 http://127.0.0.1:7890')
        if hasattr(self.proxy_entry, 'setClearButtonEnabled'):
            self.proxy_entry.setClearButtonEnabled(True)
        proxy_layout.addWidget(self.proxy_entry, 1, 1)
        proxy_layout.setColumnStretch(1, 1)
        root.addWidget(proxy_card)

        btn_row = QWidget(self)
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        self.save_btn = PrimaryPushButton(FIF.SAVE, '保存设置', btn_row)
        btn_layout.addWidget(self.save_btn)
        btn_layout.addStretch(1)
        self.save_btn.clicked.connect(self._save_config)
        root.addWidget(btn_row)
        root.addStretch(1)

        self._toggle_proxy()

    def _toggle_proxy(self, *_args):
        self.proxy_entry.setEnabled(self.proxy_enable_check.isChecked())

    def _browse_dir(self):
        start_dir = self.dir_edit.text() or os.path.expanduser('~')
        path = QFileDialog.getExistingDirectory(self, '选择下载保存目录', start_dir)
        if path:
            self.dir_edit.setText(path)

    def _load_config(self):
        try:
            with open(CONFIG_PATH, encoding='utf-8') as file:
                loaded = json.load(file)
                self.config.update(loaded)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save_config(self):
        self.config = {
            'download_dir': self.dir_edit.text(),
            'proxy_enabled': self.proxy_enable_check.isChecked(),
            'proxy_url': self.proxy_entry.text(),
            'max_concurrent': self.concurrent_spin.value(),
            'download_cover': self.cover_check.isChecked(),
            'open_folder_after': self.open_check.isChecked(),
        }
        try:
            with open(CONFIG_PATH, 'w', encoding='utf-8') as file:
                json.dump(self.config, file, ensure_ascii=False, indent=2)
            self.status_message.emit('[OK] 设置已保存')
            self._show_message('设置', '设置已保存')
        except Exception as exc:
            self._show_message('错误', f'保存失败: {exc}')

    def _show_message(self, title, content):
        dialog = MessageBox(title, content, self.window())
        dialog.hideCancelButton()
        dialog.yesButton.setText('知道了')
        dialog.exec()

    def get_config(self):
        return dict(self.config)
