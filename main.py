#!/usr/bin/env python3
"""
视频下载器 - QFluentWidgets GUI 版

使用方法:
  python main.py

首次使用需要在“登录”界面用 App 扫码登录。
"""

import os
import sys

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication

# Ensure project root is on path
from core.paths import app_dir, ensure_runtime_dirs

_project_root = app_dir()
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

ensure_runtime_dirs()

# Migrate old cookie file to new location
_old_cookie = os.path.join(_project_root, 'douyin_cookies.txt')
_new_cookie = os.path.join(_project_root, 'cookies', 'douyin_cookies.txt')
if os.path.exists(_old_cookie) and not os.path.exists(_new_cookie):
    os.makedirs(os.path.join(_project_root, 'cookies'), exist_ok=True)
    with open(_old_cookie, encoding='utf-8') as src:
        with open(_new_cookie, 'w', encoding='utf-8') as dst:
            dst.write(src.read())
    os.remove(_old_cookie)

from gui.app import MainWindow
from platforms.douyin.platform import DouyinPlatform


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setFont(QFont('Microsoft YaHei', 9))

    platforms = [
        DouyinPlatform(),
    ]

    window = MainWindow(platforms)
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
