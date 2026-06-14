from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Action,
    BodyLabel,
    FluentIcon as FIF,
    ProgressBar,
    RoundMenu,
    TableWidget,
)


class ProgressPanel(QWidget):
    """A reusable progress bar panel with status text."""

    def __init__(self, parent=None, status_label=None):
        super().__init__(parent)
        self.progress = ProgressBar(self)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

        self.status_label = status_label or BodyLabel('就绪', self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.progress)
        if status_label is None:
            layout.addWidget(self.status_label)

    def set_progress(self, value, status=''):
        self.progress.setValue(value)
        if status:
            self.status_label.setText(status)


class DownloadList(TableWidget):
    """QFluentWidgets table-based download list."""

    COLUMN_STATUS = 2
    COLUMN_PROGRESS = 3
    COLUMNS = ('#', '\u6587\u4ef6\u540d', '\u72b6\u6001', '\u8fdb\u5ea6')

    def __init__(self, parent=None):
        super().__init__(parent)
        self.item_data = {}
        self._iid_order = []
        self._iid_rows = {}

        self.setColumnCount(len(self.COLUMNS))
        self.setHorizontalHeaderLabels(self.COLUMNS)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.verticalHeader().hide()
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)

        if hasattr(self, 'setBorderVisible'):
            self.setBorderVisible(True)
        if hasattr(self, 'setBorderRadius'):
            self.setBorderRadius(8)

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)

    def _show_menu(self, pos):
        row = self.rowAt(pos.y())
        if row < 0:
            return

        self.selectRow(row)
        menu = RoundMenu(parent=self)
        delete_action = Action(FIF.DELETE, '删除', self)
        delete_action.triggered.connect(self._remove_selected)
        menu.addAction(delete_action)
        menu.exec(self.mapToGlobal(pos))

    def _remove_selected(self):
        rows = sorted({index.row() for index in self.selectedIndexes()}, reverse=True)
        for row in rows:
            self._remove_row(row, reindex=False)
        self._renumber()

    def _remove_row(self, row, reindex=True):
        if row < 0 or row >= len(self._iid_order):
            return
        iid = self._iid_order.pop(row)
        self.item_data.pop(iid, None)
        self._iid_rows.pop(iid, None)
        self.removeRow(row)
        if reindex:
            self._reindex_rows()

    def _reindex_rows(self):
        self._iid_rows = {iid: row for row, iid in enumerate(self._iid_order)}

    def _renumber(self):
        self._reindex_rows()
        for row in range(self.rowCount()):
            item = self.item(row, 0)
            if item:
                item.setText(str(row + 1))

    def add_item(
        self,
        iid,
        filename,
        platform_name,
        video_id,
        video_url,
        title,
        filepath,
        quality_options=None,
    ):
        row = self.rowCount()
        self.insertRow(row)
        quality_options = quality_options or [{'label': '\u9ed8\u8ba4', 'url': video_url}]
        values = {
            0: str(row + 1),
            1: filename,
            self.COLUMN_STATUS: '\u7b49\u5f85\u4e2d',
            self.COLUMN_PROGRESS: '0%',
        }
        for column, value in values.items():
            item = QTableWidgetItem(value)
            if column in (0, self.COLUMN_STATUS, self.COLUMN_PROGRESS):
                item.setTextAlignment(Qt.AlignCenter)
            self.setItem(row, column, item)

        self._iid_order.append(iid)
        self._iid_rows[iid] = row
        selected_quality_label = ''
        for option in quality_options:
            if option.get('url') == video_url:
                selected_quality_label = option.get('label', '')
                break

        self.item_data[iid] = {
            'platform': platform_name,
            'video_id': video_id,
            'video_url': video_url,
            'title': title,
            'filepath': filepath,
            'quality_options': quality_options,
            'quality_label': selected_quality_label,
        }

    def update_status(self, iid, status, progress_pct=''):
        row = self.row_for_iid(iid)
        if row is None:
            return

        status_item = self.item(row, self.COLUMN_STATUS)
        progress_item = self.item(row, self.COLUMN_PROGRESS)
        if status_item:
            status_item.setText(status)
        if progress_pct and progress_item:
            progress_item.setText(progress_pct)

    def status(self, iid):
        row = self.row_for_iid(iid)
        if row is None:
            return ''
        item = self.item(row, self.COLUMN_STATUS)
        return item.text() if item else ''

    def row_for_iid(self, iid):
        return self._iid_rows.get(iid)

    def remove_completed(self, done_status):
        for row in range(self.rowCount() - 1, -1, -1):
            iid = self._iid_order[row]
            if self.status(iid) == done_status:
                self._remove_row(row, reindex=False)
        self._renumber()

    def clear_all(self):
        self.setRowCount(0)
        self.item_data.clear()
        self._iid_order.clear()
        self._iid_rows.clear()

    def pending_ids(self, statuses):
        return [iid for iid in self._iid_order if self.status(iid) in statuses]
