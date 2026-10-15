"""Feedback page: controls and presentation, with all I/O delegated to services."""

import time
from pathlib import Path

from PyQt5.QtCore import QSize, Qt, QTimer
from PyQt5.QtGui import QImageReader, QPixmap
from PyQt5.QtWidgets import (
    QApplication, QButtonGroup, QFileDialog, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CardWidget, CheckBox, FluentIcon as FIF,
    LineEdit, MessageBox, PrimaryPushButton, PushButton, RadioButton,
    ScrollArea, StrongBodyLabel, SubtitleLabel, TextEdit,
)

from core.feedback import FeedbackDraft, FeedbackError, FeedbackLimits, FeedbackService
from core.feedback.models import IMAGE_MIME_TYPES
from gui.about_tab import AUTHOR_WECHAT
from gui.feedback_dialogs import FeedbackSuccessDialog
from gui.feedback_worker import FeedbackTask


PROBLEM_PLACEHOLDER = (
    '请详细描述您遇到的问题，比如点击了什么后发生了什么错误或提示。'
    '详细描述方便工作人员定位问题并修复。'
)
SUGGESTION_PLACEHOLDER = '详细描述您的建议，方便我们工作人员理解。'
class FeedbackTab(QWidget):
    """Standalone form; switching navigation keeps the current draft in memory."""

    def __init__(self, parent=None, *, service=None):
        super().__init__(parent)
        self.setObjectName('feedbackInterface')
        self._attachments = {}
        self._limits = FeedbackLimits()
        self._busy = False
        self._closing = False
        self._config_requested = False
        self._config_loading = False
        self._cooldown_until = 0.0
        self._tasks = set()
        self._service_error = ''
        try:
            self._service = service or FeedbackService()
        except (ValueError, FeedbackError):
            self._service = None
            self._service_error = '反馈服务配置异常，请联系作者。'
        self._build_ui()
        self._update_attachment_hint()
        self._cooldown_timer = QTimer(self)
        self._cooldown_timer.setInterval(1000)
        self._cooldown_timer.timeout.connect(self._update_submit_button)
        QApplication.instance().aboutToQuit.connect(self._on_quit)

    @staticmethod
    def _caption(text, parent):
        label = CaptionLabel(text, parent)
        label.setTextColor('#666666', '#b5b5b5')
        label.setWordWrap(True)
        return label

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.scroll_area = ScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName('feedbackContent')
        self.scroll_area.setWidget(content)
        self.scroll_area.setStyleSheet(
            'QScrollArea { border: none; background: transparent; }'
            'QWidget#feedbackContent { background: transparent; }'
        )
        layout = QVBoxLayout(content)
        layout.setContentsMargins(26, 18, 26, 12)
        layout.setSpacing(12)

        heading = QVBoxLayout()
        heading.setSpacing(3)
        heading.addWidget(SubtitleLabel('问题反馈', content))
        heading.addWidget(self._caption('遇到问题或有新的想法，欢迎告诉我。', content))
        layout.addLayout(heading)

        form_card = CardWidget(content)
        form = QVBoxLayout(form_card)
        form.setContentsMargins(18, 14, 18, 14)
        form.setSpacing(8)

        type_row = QHBoxLayout()
        type_row.setSpacing(18)
        type_row.addWidget(StrongBodyLabel('反馈类型', form_card))
        self.problem_radio = RadioButton('问题反馈', form_card)
        self.suggestion_radio = RadioButton('使用建议', form_card)
        self.type_group = QButtonGroup(self)
        self.type_group.addButton(self.problem_radio)
        self.type_group.addButton(self.suggestion_radio)
        self.problem_radio.setChecked(True)
        type_row.addWidget(self.problem_radio)
        type_row.addWidget(self.suggestion_radio)
        type_row.addStretch(1)
        form.addLayout(type_row)

        self.description_edit = TextEdit(form_card)
        self.description_edit.setAcceptRichText(False)
        self.problem_radio.toggled.connect(self._update_description_placeholder)
        self._update_description_placeholder()
        self.description_edit.setFixedHeight(120)
        self.description_edit.setAccessibleName('详细描述')
        self.description_edit.textChanged.connect(self._update_content_count)
        form.addWidget(self.description_edit)
        self.content_count_label = self._caption(f'0 / {self._limits.max_content_length}', form_card)
        self.content_count_label.setAlignment(Qt.AlignRight)
        form.addWidget(self.content_count_label)

        contact_row = QHBoxLayout()
        contact_row.setSpacing(16)
        contact_row.addWidget(BodyLabel('联系微信（选填）', form_card))
        self.wechat_edit = LineEdit(form_card)
        self.wechat_edit.setPlaceholderText('填写微信号，方便联系你')
        self.wechat_edit.setFixedWidth(240)
        self.wechat_edit.setMaxLength(100)
        self.wechat_edit.setClearButtonEnabled(True)
        self.wechat_edit.setAccessibleName('联系微信（选填）')
        contact_row.addWidget(self.wechat_edit)
        contact_row.addStretch(1)
        form.addLayout(contact_row)
        layout.addWidget(form_card)

        attachment_card = CardWidget(content)
        attachments = QVBoxLayout(attachment_card)
        attachments.setContentsMargins(18, 14, 18, 14)
        attachments.setSpacing(8)
        attachment_header = QHBoxLayout()
        attachment_header.addWidget(StrongBodyLabel('截图附件', attachment_card))
        attachment_header.addStretch(1)
        self.add_image_button = PushButton(FIF.ADD, '添加截图', attachment_card)
        self.add_image_button.clicked.connect(self._choose_images)
        attachment_header.addWidget(self.add_image_button)
        attachments.addLayout(attachment_header)
        self.attachment_hint = self._caption('', attachment_card)
        attachments.addWidget(self.attachment_hint)
        self.empty_label = self._caption('暂未添加截图，可附上出错画面帮助说明。', attachment_card)
        attachments.addWidget(self.empty_label)
        self.attachment_rows = QVBoxLayout()
        self.attachment_rows.setSpacing(6)
        attachments.addLayout(self.attachment_rows)

        log_row = QHBoxLayout()
        self.log_check = CheckBox('附带近期诊断日志', attachment_card)
        self.log_check.setChecked(True)
        self.log_check.setEnabled(False)
        log_row.addWidget(self.log_check)
        log_row.addStretch(1)
        attachments.addLayout(log_row)
        layout.addWidget(attachment_card)
        layout.addStretch(1)
        root.addWidget(self.scroll_area, 1)

        # Keep the primary action visible when attachment rows make the form scroll.
        footer = QHBoxLayout()
        footer.setContentsMargins(26, 8, 26, 14)
        footer.setSpacing(12)
        self.feedback_status = self._caption('', self)
        self.feedback_status.setTextFormat(Qt.PlainText)
        self.feedback_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        footer.addWidget(self.feedback_status, 1)
        self.submit_button = PrimaryPushButton('提交', self)
        self.submit_button.setMinimumWidth(112)
        self.submit_button.clicked.connect(self._submit)
        footer.addWidget(self.submit_button)
        root.addLayout(footer)

    def _update_description_placeholder(self):
        self.description_edit.setPlaceholderText(
            PROBLEM_PLACEHOLDER if self.problem_radio.isChecked() else SUGGESTION_PLACEHOLDER
        )

    def _update_content_count(self):
        count = len(self.description_edit.toPlainText())
        maximum = self._limits.max_content_length
        self.content_count_label.setText(f'{count} / {maximum}')
        self.content_count_label.setTextColor(
            '#c42b1c' if count > maximum else '#666666',
            '#ff9999' if count > maximum else '#b5b5b5',
        )

    def _update_attachment_hint(self):
        limit = self._limits.max_images
        types = ' / '.join(ext[1:].upper() for ext in self._limits.extensions if ext in IMAGE_MIME_TYPES)
        single_mb = self._limits.max_file_bytes / 1024 / 1024
        total_mb = self._limits.max_total_bytes / 1024 / 1024
        self.attachment_hint.setText(
            f'已添加 {len(self._attachments)} / {limit} 张 · {types or "暂不支持截图"} · '
            f'单张 ≤ {single_mb:g} MB，附件合计 ≤ {total_mb:g} MB，已为日志预留 1 个名额'
        )
        self.empty_label.setVisible(not self._attachments)
        self.add_image_button.setEnabled(not self._busy and len(self._attachments) < limit and bool(types))

    def _choose_images(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, '添加问题截图', '', '截图 (*.jpg *.jpeg *.png *.JPG *.JPEG *.PNG)',
        )
        errors = []
        for filename in paths:
            path = Path(filename).resolve()
            if path in self._attachments:
                continue
            limit = self._limits.max_images
            if len(self._attachments) >= limit:
                errors.append(f'最多添加 {limit} 张截图，其余文件未添加。')
                break
            try:
                size = path.stat().st_size
                if path.suffix.lower() not in IMAGE_MIME_TYPES or path.suffix.lower() not in self._limits.extensions:
                    raise ValueError('当前服务不支持该截图类型')
                if size <= 0 or size > self._limits.max_file_bytes:
                    raise ValueError(f'图片不能为空，且单张不能超过 {self._limits.max_file_bytes / 1024 / 1024:g} MB')
                if size + sum(item['size'] for item in self._attachments.values()) > self._limits.image_budget:
                    raise ValueError('截图合计过大，请减少截图大小，为诊断日志保留空间')
                reader = QImageReader(str(path))
                if not reader.canRead() or bytes(reader.format()).lower() not in (b'jpeg', b'jpg', b'png'):
                    raise ValueError('图片格式无效，请选择有效的 JPG 或 PNG 图片')
                reader.setAutoTransform(True)
                reader.setScaledSize(reader.size().scaled(QSize(40, 40), Qt.KeepAspectRatio))
                thumbnail = reader.read()
                if thumbnail.isNull():
                    raise ValueError('图片无法读取，请重新选择')
                self._add_image_row(path, size, QPixmap.fromImage(thumbnail))
            except (OSError, ValueError) as exc:
                errors.append(f'{path.name}：{exc}')
        self._update_attachment_hint()
        if errors:
            self._show_message('部分截图未添加', '\n'.join(errors))

    def _add_image_row(self, path, size, thumbnail):
        row = QWidget(self)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 2, 0, 2)
        row_layout.setSpacing(10)
        image = QLabel(row)
        image.setFixedSize(40, 40)
        image.setAlignment(Qt.AlignCenter)
        image.setPixmap(thumbnail)
        row_layout.addWidget(image)
        name = BodyLabel(path.name, row)
        name.setTextFormat(Qt.PlainText)
        name.setToolTip(str(path))
        name.setMinimumWidth(0)
        name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row_layout.addWidget(name, 1)
        if size < 1024:
            display_size = f'{size} B'
        elif size < 1024 * 1024:
            display_size = f'{size / 1024:.0f} KB'
        else:
            display_size = f'{size / 1024 / 1024:.1f} MB'
        row_layout.addWidget(self._caption(display_size, row))
        remove = PushButton('移除', row)
        remove.clicked.connect(lambda _checked=False, selected=path: self._remove_image(selected))
        row_layout.addWidget(remove)
        self.attachment_rows.addWidget(row)
        self._attachments[path] = {'size': size, 'row': row, 'remove_button': remove}

    def _remove_image(self, path):
        item = self._attachments.pop(path)
        self.attachment_rows.removeWidget(item['row'])
        item['row'].deleteLater()
        self._update_attachment_hint()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._config_requested:
            self._config_requested = True
            QTimer.singleShot(0, self._load_limits)

    def _run_task(self, operation, on_complete):
        task = FeedbackTask(operation, self)
        self._tasks.add(task)

        def completed(result, error):
            self._tasks.discard(task)
            task.deleteLater()
            if not self._closing:
                on_complete(result, error)

        task.completed.connect(completed)
        task.start()

    def _load_limits(self):
        if self._closing:
            return
        if self._service is None:
            self.feedback_status.setText(self._service_error)
            self._update_submit_button()
            return
        self._config_loading = True
        self.feedback_status.setText('正在获取上传限制…')
        self._update_submit_button()
        self._run_task(self._service.fetch_limits, self._limits_loaded)

    def _limits_loaded(self, limits, error):
        self._config_loading = False
        if error is None:
            self._limits = limits
            self.feedback_status.setText('')
        else:
            self.feedback_status.setText('暂未获取上传限制，将按默认限制检查。')
        self._update_content_count()
        self._update_attachment_hint()
        self._update_submit_button()

    def _draft(self):
        return FeedbackDraft(
            feedback_type='problem' if self.problem_radio.isChecked() else 'suggestion',
            content=self.description_edit.toPlainText(), wechat=self.wechat_edit.text(),
            image_paths=tuple(self._attachments),
        )

    def _submit(self):
        if self._busy or self._closing or self._config_loading or time.monotonic() < self._cooldown_until:
            return
        if self._service is None:
            self._show_failure(self._service_error)
            return
        draft = self._draft()
        try:
            draft.validate(self._limits)
        except FeedbackError as exc:
            self._show_message('请检查反馈内容', str(exc))
            self.description_edit.setFocus()
            return
        self._set_busy(True)
        self.feedback_status.setText('正在提交反馈，请稍候…')
        self._run_task(lambda: self._service.submit(draft), self._submission_finished)

    def _set_busy(self, busy):
        self._busy = busy
        for control in (self.problem_radio, self.suggestion_radio, self.description_edit, self.wechat_edit):
            control.setEnabled(not busy)
        for item in self._attachments.values():
            item['remove_button'].setEnabled(not busy)
        self._update_attachment_hint()
        self._update_submit_button()

    def _update_submit_button(self):
        remaining = max(0, int(self._cooldown_until - time.monotonic() + 0.999))
        if not remaining:
            self._cooldown_timer.stop()
        self.submit_button.setText('正在提交…' if self._busy else (f'提交（{remaining}s）' if remaining else '提交'))
        self.submit_button.setEnabled(not self._busy and not self._config_loading and not remaining
                                      and not self._closing and self._service is not None)

    def _submission_finished(self, receipt, error):
        self._limits = self._service.limits
        self._update_content_count()
        if error is not None:
            self.feedback_status.setText(str(error))
            if error.retry_after:
                self._start_cooldown(error.retry_after)
            self._set_busy(False)
            self._show_failure(str(error))
            return
        self.description_edit.clear()
        for path in list(self._attachments):
            self._remove_image(path)
        self.feedback_status.setText('')
        self._start_cooldown(self._limits.uuid_min_interval_seconds)
        self._set_busy(False)
        self._show_success()

    def _start_cooldown(self, seconds):
        self._cooldown_until = time.monotonic() + seconds
        self._cooldown_timer.start()

    def _show_success(self):
        dialog = FeedbackSuccessDialog(self.window())
        dialog.exec()

    def _show_failure(self, reason):
        reason = str(reason).strip().rstrip('。.!！') or '未知原因'
        self._show_message(
            '发送失败', f'发送失败，失败原因：{reason}。可以联系作者微信{AUTHOR_WECHAT}反馈',
        )

    def _on_quit(self):
        self._closing = True
        self._cooldown_timer.stop()

    def _show_message(self, title, content):
        dialog = MessageBox(title, content, self.window())
        dialog.contentLabel.setTextFormat(Qt.PlainText)
        dialog.hideCancelButton()
        dialog.yesButton.setText('知道了')
        dialog.exec()
