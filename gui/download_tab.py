import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as datetime_time

from PyQt5.QtCore import QDate, Qt, pyqtSignal
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QHBoxLayout,
    QShortcut,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    DateEdit,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    RadioButton,
    StrongBodyLabel,
    TextEdit,
)

from core.cookie_manager import load_cookies
from core.downloader import download_video
from core.paths import runtime_path
from gui.widgets import DownloadList, ProgressPanel

_DEFAULT_DL_DIR = runtime_path('downloads')

STATUS_PENDING = '等待中'
STATUS_DOWNLOADING = '下载中'
STATUS_DONE = '完成'
STATUS_FAILED = '失败'

QUALITY_PREFERENCES = (
    ('highest', '\u6700\u9ad8\u53ef\u7528'),
    ('1080', '1080p'),
    ('720', '720p'),
    ('540', '540p'),
    ('480', '480p'),
    ('lowest', '\u6700\u4f4e\u53ef\u7528'),
    ('default', '\u9ed8\u8ba4\u6e90'),
)


class DownloadTab(QWidget):
    """Page for downloading videos from supported platforms."""

    status_message = pyqtSignal(str)
    add_item_requested = pyqtSignal(object, object)
    parse_done_requested = pyqtSignal()
    clear_url_requested = pyqtSignal()
    show_error_requested = pyqtSignal(str, str)
    show_warning_requested = pyqtSignal(str, str)
    show_info_requested = pyqtSignal(str, str)
    download_started = pyqtSignal(str)
    download_progress = pyqtSignal(str, object, object, str)
    download_done = pyqtSignal(str, str, str)
    download_failed = pyqtSignal(str, str, str)
    open_folder_requested = pyqtSignal(str)

    def __init__(self, parent, platforms, status_callback, settings_getter):
        super().__init__(parent)
        self.setObjectName('downloadInterface')
        self.platforms = {p.name: p for p in platforms}
        self.platform_list = list(self.platforms.values())
        self.status_callback = status_callback
        self.settings_getter = settings_getter
        self._max_workers = self._settings_max_concurrent()
        self._executor = ThreadPoolExecutor(max_workers=self._max_workers)
        self._done_count = 0
        self._total_count = 0
        self._success_count = 0
        self._failed_count = 0
        self._summary_shown = False
        self._dl_progress = {}
        self._download_success_dirs = []
        self._download_batch_label = '全部下载'

        self.status_message.connect(status_callback)
        self.add_item_requested.connect(self._add_item)
        self.parse_done_requested.connect(self._all_parse_done)
        self.clear_url_requested.connect(self.url_text_clear)
        self.show_error_requested.connect(self._show_error)
        self.show_warning_requested.connect(self._show_warning)
        self.show_info_requested.connect(self._show_info)
        self.download_started.connect(self._on_dl_started)
        self.download_progress.connect(self._on_dl_progress)
        self.download_done.connect(self._on_dl_done)
        self.download_failed.connect(self._on_dl_failed)
        self.open_folder_requested.connect(self._open_folder)

        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 18)
        root.setSpacing(14)

        input_card = CardWidget(self)
        input_layout = QGridLayout(input_card)
        input_layout.setContentsMargins(18, 16, 18, 18)
        input_layout.setHorizontalSpacing(12)
        input_layout.setVerticalSpacing(12)

        input_layout.addWidget(BodyLabel('平台:', input_card), 0, 0, Qt.AlignRight)
        self.platform_combo = ComboBox(input_card)
        names = [p.display_name for p in self.platform_list]
        self.platform_combo.addItems(names)
        if names:
            self.platform_combo.setCurrentText(names[0])
        input_layout.addWidget(self.platform_combo, 0, 1)

        input_layout.addWidget(BodyLabel('类型:', input_card), 0, 2, Qt.AlignRight)
        media_box = QWidget(input_card)
        media_layout = QHBoxLayout(media_box)
        media_layout.setContentsMargins(0, 0, 0, 0)
        media_layout.setSpacing(10)
        self.video_radio = RadioButton('视频', media_box)
        self.audio_radio = RadioButton('音频', media_box)
        self.video_radio.setChecked(True)
        self.media_group = QButtonGroup(self)
        self.media_group.addButton(self.video_radio)
        self.media_group.addButton(self.audio_radio)
        media_layout.addWidget(self.video_radio)
        media_layout.addWidget(self.audio_radio)
        input_layout.addWidget(media_box, 0, 3)

        input_layout.addWidget(BodyLabel('\u753b\u8d28:', input_card), 0, 4, Qt.AlignRight)
        self.quality_combo = ComboBox(input_card)
        self.quality_combo.addItems([label for _, label in QUALITY_PREFERENCES])
        default_quality_index = next(
            (
                index for index, (key, _label) in enumerate(QUALITY_PREFERENCES)
                if key == 'default'
            ),
            0,
        )
        self.quality_combo.setCurrentIndex(default_quality_index)
        self.quality_combo.setMinimumWidth(126)
        self.quality_combo.currentIndexChanged.connect(self._apply_quality_to_pending_items)
        self.video_radio.toggled.connect(self._sync_quality_enabled)
        self.audio_radio.toggled.connect(self._sync_quality_enabled)
        input_layout.addWidget(self.quality_combo, 0, 5)

        filter_box = QWidget(input_card)
        filter_layout = QHBoxLayout(filter_box)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(8)
        self.date_filter_check = CheckBox('按发布日期筛选', filter_box)
        today = QDate.currentDate()
        self.start_date_edit = DateEdit(filter_box)
        self.start_date_edit.setDisplayFormat('yyyy-MM-dd')
        self.start_date_edit.setDate(today.addDays(-30))
        self.end_date_edit = DateEdit(filter_box)
        self.end_date_edit.setDisplayFormat('yyyy-MM-dd')
        self.end_date_edit.setDate(today)
        self.date_filter_check.stateChanged.connect(self._sync_date_filter_enabled)
        filter_layout.addWidget(self.date_filter_check)
        filter_layout.addWidget(BodyLabel('开始:', filter_box))
        filter_layout.addWidget(self.start_date_edit)
        filter_layout.addWidget(BodyLabel('结束:', filter_box))
        filter_layout.addWidget(self.end_date_edit)
        filter_layout.addStretch(1)
        input_layout.addWidget(filter_box, 1, 0, 1, 7)

        self.url_text = TextEdit(input_card)
        self.url_text.setPlaceholderText('粘贴视频链接、图文链接或作者主页链接，多个链接请每行一个')
        self.url_text.setFixedHeight(112)
        input_layout.addWidget(self.url_text, 2, 0, 1, 6)

        self.parse_btn = PrimaryPushButton(FIF.SEARCH, '解析', input_card)
        self.parse_btn.clicked.connect(self._parse_url)
        input_layout.addWidget(self.parse_btn, 2, 6, Qt.AlignTop)
        QShortcut(QKeySequence('Ctrl+Return'), self.url_text, self._parse_url)

        input_layout.setColumnStretch(5, 1)
        self._sync_quality_enabled()
        self._sync_date_filter_enabled()
        root.addWidget(input_card)

        list_card = CardWidget(self)
        list_layout = QVBoxLayout(list_card)
        list_layout.setContentsMargins(18, 16, 18, 18)
        list_layout.setSpacing(12)
        list_layout.addWidget(StrongBodyLabel('下载列表', list_card))

        self.download_list = DownloadList(list_card)
        self.download_list.setMinimumHeight(180)
        self.download_list.setMaximumHeight(240)
        self.download_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        list_layout.addWidget(self.download_list)

        btn_row = QWidget(list_card)
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(10)
        self.dl_all_btn = PrimaryPushButton(FIF.CLOUD_DOWNLOAD, '全部下载', btn_row)
        self.dl_all_btn.clicked.connect(self._start_all_downloads)
        self.clear_btn = PushButton(FIF.DELETE, '清除已完成', btn_row)
        self.clear_btn.clicked.connect(self._clear_completed)
        self.clear_list_btn = PushButton(FIF.DELETE, '清除列表', btn_row)
        self.clear_list_btn.clicked.connect(self._clear_download_list)
        self.retry_failed_btn = PushButton(FIF.SYNC, '失败重试', btn_row)
        self.retry_failed_btn.clicked.connect(self._retry_failed_downloads)
        btn_layout.addWidget(self.dl_all_btn)
        btn_layout.addWidget(self.clear_btn)
        btn_layout.addWidget(self.clear_list_btn)
        btn_layout.addWidget(self.retry_failed_btn)
        btn_layout.addStretch(1)
        self.download_status_label = BodyLabel('就绪', btn_row)
        self.download_status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.download_status_label.setMinimumWidth(180)
        self.download_status_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        btn_layout.addWidget(self.download_status_label)
        list_layout.addWidget(btn_row)

        root.addWidget(list_card)

        self.progress_panel = ProgressPanel(self, status_label=self.download_status_label)
        root.addWidget(self.progress_panel)

    def _get_current_platform(self):
        name = self.platform_combo.currentText()
        for platform in self.platform_list:
            if platform.display_name == name:
                return platform
        return self.platform_list[0] if self.platform_list else None

    def _selected_media_type(self):
        return 'audio' if self.audio_radio.isChecked() else 'video'

    def _selected_quality_preference(self):
        index = self.quality_combo.currentIndex()
        if 0 <= index < len(QUALITY_PREFERENCES):
            return QUALITY_PREFERENCES[index][0]
        return 'highest'

    def _sync_quality_enabled(self, *_):
        self.quality_combo.setEnabled(self.video_radio.isChecked())

    def _sync_date_filter_enabled(self, *_):
        enabled = self.date_filter_check.isChecked()
        self.start_date_edit.setEnabled(enabled)
        self.end_date_edit.setEnabled(enabled)

    def _profile_date_range(self):
        if not self.date_filter_check.isChecked():
            return None, None, ''

        start_date = self.start_date_edit.date().toPyDate()
        end_date = self.end_date_edit.date().toPyDate()
        if start_date > end_date:
            raise ValueError('开始日期不能晚于结束日期')

        start_time = int(datetime.combine(start_date, datetime_time.min).timestamp())
        end_time = int(datetime.combine(end_date, datetime_time.max).timestamp())
        label = f'{start_date:%Y-%m-%d} 至 {end_date:%Y-%m-%d}'
        return start_time, end_time, label

    def _video_quality_options(self, info):
        variants = getattr(info, 'video_variants', None) or info.extra.get('video_variants') or []
        options = []
        seen_urls = set()

        for variant in variants:
            if isinstance(variant, dict):
                label = variant.get('label') or '\u9ed8\u8ba4'
                url = variant.get('url')
                height = self._safe_int(variant.get('height'))
                bitrate = self._safe_int(variant.get('bitrate'))
                quality = variant.get('quality')
            else:
                label = getattr(variant, 'label', None) or '\u9ed8\u8ba4'
                url = getattr(variant, 'url', None)
                height = self._safe_int(getattr(variant, 'height', None))
                bitrate = self._safe_int(getattr(variant, 'bitrate', None))
                quality = getattr(variant, 'quality', None)

            if not url or url in seen_urls:
                continue
            options.append({
                'label': label,
                'url': url,
                'height': height,
                'bitrate': bitrate,
                'quality': quality,
            })
            seen_urls.add(url)

        if info.video_url and info.video_url not in seen_urls:
            options.append({
                'label': '\u9ed8\u8ba4',
                'url': info.video_url,
                'height': None,
                'bitrate': None,
                'quality': 'default',
            })

        options.sort(
            key=lambda item: (
                item.get('height') or 0,
                item.get('bitrate') or 0,
            ),
            reverse=True,
        )
        return options or [{
            'label': '\u9ed8\u8ba4',
            'url': info.video_url,
            'height': None,
            'bitrate': None,
            'quality': 'default',
        }]

    def _select_quality_option(self, quality_options):
        quality_options = quality_options or []
        if not quality_options:
            return {'label': '\u9ed8\u8ba4', 'url': ''}

        preference = self._selected_quality_preference()
        known_resolution_options = [
            option for option in quality_options if option.get('height')
        ]

        if preference == 'highest':
            return quality_options[0]

        if preference == 'lowest':
            return known_resolution_options[-1] if known_resolution_options else quality_options[-1]

        if preference == 'default':
            return next(
                (
                    option for option in quality_options
                    if option.get('quality') == 'default'
                    or option.get('label') in ('default', '\u9ed8\u8ba4', '\u9ed8\u8ba4\u6e90')
                ),
                quality_options[0],
            )

        target_height = self._safe_int(preference)
        if not target_height:
            return quality_options[0]

        exact_options = [
            option for option in known_resolution_options
            if option.get('height') == target_height
        ]
        if exact_options:
            return exact_options[0]

        lower_or_equal_options = [
            option for option in known_resolution_options
            if option.get('height') <= target_height
        ]
        if lower_or_equal_options:
            return lower_or_equal_options[0]

        return known_resolution_options[-1] if known_resolution_options else quality_options[0]

    def _apply_quality_to_pending_items(self, *_):
        if not hasattr(self, 'download_list'):
            return

        for iid, data in self.download_list.item_data.items():
            if self.download_list.status(iid) not in (STATUS_PENDING, STATUS_FAILED):
                continue

            option = self._select_quality_option(data.get('quality_options'))
            url = option.get('url')
            if url:
                data['video_url'] = url
                data['quality_label'] = option.get('label', '')

    @staticmethod
    def _safe_int(value):
        try:
            if value is None or value == '':
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    def _parse_url(self):
        raw = self.url_text.toPlainText().strip()
        urls = [line.strip() for line in raw.splitlines() if line.strip()]
        if not urls:
            self._show_warning('提示', '请先粘贴视频或主页链接')
            return

        platform = self._get_current_platform()
        if not platform:
            self._show_error('错误', '没有可用的平台')
            return

        try:
            start_time, end_time, date_range_label = self._profile_date_range()
        except ValueError as exc:
            self._show_warning('日期范围', str(exc))
            return
        date_filter_enabled = bool(date_range_label)

        self.parse_btn.setEnabled(False)
        self.parse_btn.setText('解析中...')
        self.url_text.setEnabled(False)
        self.status_message.emit(f'[*] 正在解析 {len(urls)} 个链接...')

        def _do_batch_parse():
            cookies = load_cookies(platform.name)
            if not cookies:
                self.parse_done_requested.emit()
                self.show_error_requested.emit('错误', '未登录，请先在“登录”界面登录')
                return

            total = len(urls)
            success = 0
            failed = 0
            for index, url in enumerate(urls):
                try:
                    video_id = platform.extract_video_id(url)
                    if video_id:
                        info = platform.get_video_info(video_id, cookies)
                        self.add_item_requested.emit(info, platform)
                        self.status_message.emit(f'[OK] ({index + 1}/{total}) {info.title[:30]}')
                        success += 1
                        continue

                    profile_id = platform.extract_profile_id(url)
                    if profile_id:
                        if date_filter_enabled:
                            self.status_message.emit(
                                f'[*] ({index + 1}/{total}) 正在解析主页作品，'
                                f'仅保留 {date_range_label} 的作品...'
                            )
                        else:
                            self.status_message.emit(f'[*] ({index + 1}/{total}) 正在解析主页作品...')

                        def _profile_progress(found, page, has_more, link_index=index):
                            suffix = '，继续翻页' if has_more else ''
                            item_label = '符合日期范围的作品' if date_filter_enabled else '视频'
                            self.status_message.emit(
                                f'[*] ({link_index + 1}/{total}) 主页第 {page} 页，'
                                f'已找到 {found} 个{item_label}{suffix}'
                            )

                        if date_filter_enabled:
                            infos = platform.get_profile_videos(
                                profile_id,
                                cookies,
                                _profile_progress,
                                start_time=start_time,
                                end_time=end_time,
                            )
                        else:
                            infos = platform.get_profile_videos(profile_id, cookies, _profile_progress)
                        if not infos:
                            if date_filter_enabled:
                                self.status_message.emit(
                                    f'[!] ({index + 1}/{total}) 指定日期范围内没有可下载视频'
                                )
                            else:
                                self.status_message.emit(f'[!] ({index + 1}/{total}) 主页没有可下载视频')
                            failed += 1
                            continue

                        for info in infos:
                            self.add_item_requested.emit(info, platform)
                        success += len(infos)
                        if date_filter_enabled:
                            self.status_message.emit(
                                f'[OK] ({index + 1}/{total}) 主页解析完成，'
                                f'新增 {len(infos)} 个符合日期范围的视频'
                            )
                        else:
                            self.status_message.emit(
                                f'[OK] ({index + 1}/{total}) 主页解析完成，新增 {len(infos)} 个视频'
                            )
                        continue

                    self.status_message.emit(
                        f'[!] ({index + 1}/{total}) 无法识别链接格式: {url[:50]}'
                    )
                    failed += 1
                except Exception as exc:
                    self.status_message.emit(f'[!] ({index + 1}/{total}) {exc}')
                    failed += 1

            if failed == 0:
                self.status_message.emit(f'[OK] 全部解析成功，新增 {success} 个视频')
                self.parse_done_requested.emit()
                self.clear_url_requested.emit()
            elif success > 0:
                self.status_message.emit(
                    f'[!] 解析完成: 新增 {success} 个视频, {failed} 个链接失败'
                )
                self.parse_done_requested.emit()
            else:
                self.status_message.emit(f'[!] 全部失败 ({failed} 个链接)')
                self.parse_done_requested.emit()

        threading.Thread(target=_do_batch_parse, daemon=True).start()

    def _add_item(self, info, platform):
        dl_dir = self._download_dir_from_settings()
        media_type = self._selected_media_type()

        if media_type == 'audio' and info.audio_url:
            url = info.audio_url
            ext = '.mp3'
            quality_options = [{'label': '\u97f3\u9891', 'url': url}]
        else:
            quality_options = self._video_quality_options(info)
            selected_quality = self._select_quality_option(quality_options)
            url = selected_quality['url']
            ext = '.mp4'

        filename = info.extra.get('filename', f'{info.video_id}{ext}')
        base = os.path.splitext(filename)[0]
        filepath = os.path.join(dl_dir, base + ext)

        iid = str(uuid.uuid4())
        self.download_list.add_item(
            iid=iid,
            filename=info.extra.get('filename', info.title),
            platform_name=platform.name,
            video_id=info.video_id,
            video_url=url,
            title=info.title,
            filepath=filepath,
            quality_options=quality_options,
        )

    def _download_dir_from_settings(self, settings=None):
        settings = settings if settings is not None else self.settings_getter()
        return settings.get('download_dir', '') or _DEFAULT_DL_DIR

    def apply_settings(self, config):
        dl_dir = self._download_dir_from_settings(config)
        for iid, data in self.download_list.item_data.items():
            if self.download_list.status(iid) not in (STATUS_PENDING, STATUS_FAILED):
                continue
            filename = os.path.basename(data.get('filepath') or '')
            if filename:
                data['filepath'] = os.path.join(dl_dir, filename)

    def _all_parse_done(self):
        self.parse_btn.setEnabled(True)
        self.parse_btn.setText('解析')
        self.url_text.setEnabled(True)

    def url_text_clear(self):
        self.url_text.clear()

    def _start_all_downloads(self):
        self._apply_quality_to_pending_items()
        pending = self.download_list.pending_ids((STATUS_PENDING, STATUS_FAILED))
        if not pending:
            self._show_info('提示', '没有等待下载的项目')
            return

        self._start_download_batch(pending, '全部下载', renumber=True)

    def _retry_failed_downloads(self):
        failed = self.download_list.pending_ids((STATUS_FAILED,))
        if not failed:
            self._show_info('提示', '全部下载成功，没有下载失败的')
            return

        self._start_download_batch(failed, '失败重试', renumber=False)

    def _start_download_batch(self, iids, batch_label, renumber=True):
        self._sync_executor_workers()
        self._total_count = len(iids)
        self._done_count = 0
        self._success_count = 0
        self._failed_count = 0
        self._summary_shown = False
        self._dl_progress = {iid: {'down': 0, 'total': 0} for iid in iids}
        self._download_success_dirs = []
        self._download_batch_label = batch_label
        self._set_download_buttons_enabled(False)

        if renumber:
            import re as _re

            pad_width = len(str(len(iids)))
            for index, iid in enumerate(iids, start=1):
                data = self.download_list.item_data[iid]
                prefix = f'{index:0{pad_width}d}_'
                current_name = os.path.basename(data['filepath'])
                if not _re.match(r'^\d+_', current_name):
                    dir_name = os.path.dirname(data['filepath'])
                    data['filepath'] = os.path.join(dir_name, prefix + current_name)

        first = self.download_list.item_data[iids[0]]
        platform = self.platforms.get(first['platform'])
        headers = None
        if platform:
            cookies = load_cookies(platform.name)
            headers = platform.build_download_headers(cookies or {})

        for iid in iids:
            data = self.download_list.item_data[iid]
            self._executor.submit(self._download_one, iid, data, headers)
        self.status_message.emit(
            f'[*] {batch_label}已加入下载队列 {len(iids)} 个，最多同时下载 {self._max_workers} 个'
        )

    def _set_download_buttons_enabled(self, enabled):
        self.dl_all_btn.setEnabled(enabled)
        self.clear_list_btn.setEnabled(enabled)
        self.retry_failed_btn.setEnabled(enabled)

    def _settings_max_concurrent(self):
        try:
            value = int(self.settings_getter().get('max_concurrent', 2))
        except (TypeError, ValueError):
            value = 2
        return max(1, min(value, 10))

    def _sync_executor_workers(self):
        max_workers = self._settings_max_concurrent()
        if max_workers == self._max_workers:
            return

        old_executor = self._executor
        self._max_workers = max_workers
        self._executor = ThreadPoolExecutor(max_workers=self._max_workers)
        old_executor.shutdown(wait=False, cancel_futures=False)

    def _download_one(self, iid, data, headers):
        title_trunc = data['title'][:30]
        self.download_started.emit(iid)

        last_emit_time = 0.0
        last_percent = -1

        def progress_cb(downloaded, total):
            nonlocal last_emit_time, last_percent
            downloaded = int(downloaded)
            total = int(total or 0)
            now = time.monotonic()
            percent = int(downloaded * 100 / total) if total else None

            is_done = bool(total and downloaded >= total)
            percent_changed = percent is not None and percent != last_percent
            interval_elapsed = now - last_emit_time >= 0.5
            unknown_total_interval = percent is None and interval_elapsed

            if not (is_done or (percent_changed and interval_elapsed) or unknown_total_interval):
                return

            last_emit_time = now
            if percent is not None:
                last_percent = percent
            self.download_progress.emit(iid, downloaded, total, title_trunc)

        try:
            platform = self.platforms.get(data['platform'])
            if not headers and platform:
                cookies = load_cookies(platform.name)
                headers = platform.build_download_headers(cookies or {})

            download_video(data['video_url'], data['filepath'], headers or {}, progress_cb)
            self.download_done.emit(iid, title_trunc, data['filepath'])
        except Exception as exc:
            self.download_failed.emit(iid, title_trunc, str(exc))

    def _on_dl_started(self, iid):
        self.download_list.update_status(iid, STATUS_DOWNLOADING)

    def _on_dl_progress(self, iid, downloaded, total, title_trunc):
        self._dl_progress[iid] = {'down': downloaded, 'total': total}
        item_pct = f'{int(downloaded * 100 / total)}%' if total else ''
        self.download_list.update_status(iid, STATUS_DOWNLOADING, item_pct)

    def _on_dl_done(self, iid, title_trunc, filepath):
        self.download_list.update_status(iid, STATUS_DONE, '100%')
        self._done_count += 1
        self._success_count += 1
        download_dir = os.path.dirname(filepath)
        if download_dir and not self._download_success_dirs:
            self._download_success_dirs.append(download_dir)
        self.status_message.emit(f'[OK] 下载完成: {title_trunc}')

        done = self._done_count
        pct = int(done * 100 / self._total_count) if self._total_count else 100
        if done >= self._total_count:
            if self._download_batch_label == '失败重试':
                status = f'失败重试完成 ({done} 个)'
            else:
                status = f'全部下载完成 ({done} 个)'
            self._finish_download_batch(status)
        else:
            self.progress_panel.set_progress(pct, f'完成 {done}/{self._total_count} 个')

    def _on_dl_failed(self, iid, title_trunc, error):
        self.download_list.update_status(iid, STATUS_FAILED)
        self._done_count += 1
        self._failed_count += 1
        self.status_message.emit(f'[!] 下载失败: {title_trunc}')
        if error:
            self.status_message.emit(f'[!] 下载失败详情: {error}')

        done = self._done_count
        pct = int(done * 100 / self._total_count) if self._total_count else 100
        if done >= self._total_count:
            if self._download_batch_label == '失败重试':
                status = f'失败重试完成 ({done} 个)'
            else:
                status = f'下载完成 ({done} 个)'
            self._finish_download_batch(status)
        else:
            self.progress_panel.set_progress(pct, f'完成 {done}/{self._total_count} 个')

    def _finish_download_batch(self, status_text):
        self._set_download_buttons_enabled(True)
        self.progress_panel.set_progress(100, status_text)
        if self._summary_shown:
            return
        self._summary_shown = True
        self._show_download_summary(
            self._total_count,
            self._success_count,
            self._failed_count,
        )
        if self.settings_getter().get('open_folder_after') and self._download_success_dirs:
            self.open_folder_requested.emit(self._download_success_dirs[0])

    def _show_download_summary(self, total, success, failed):
        content = f'此次下载任务 {total} 个\n成功 {success} 个\n失败 {failed} 个'
        dialog = MessageBox('下载完成', content, self.window())
        dialog.hideCancelButton()
        dialog.yesButton.setText('确定')
        dialog.exec()

    def _clear_completed(self):
        self.download_list.remove_completed(STATUS_DONE)

    def _clear_download_list(self):
        self.download_list.clear_all()
        self._dl_progress = {}
        self.progress_panel.set_progress(0, '就绪')

    def _open_folder(self, path):
        import subprocess

        if path:
            subprocess.Popen(['explorer', os.path.normpath(path)])

    def _show_error(self, title, content):
        InfoBar.error(
            title=title,
            content=content,
            parent=self.window(),
            position=InfoBarPosition.TOP_RIGHT,
            duration=3000,
        )

    def _show_warning(self, title, content):
        InfoBar.warning(
            title=title,
            content=content,
            parent=self.window(),
            position=InfoBarPosition.TOP_RIGHT,
            duration=2500,
        )

    def _show_info(self, title, content):
        InfoBar.info(
            title=title,
            content=content,
            parent=self.window(),
            position=InfoBarPosition.TOP_RIGHT,
            duration=2200,
        )
