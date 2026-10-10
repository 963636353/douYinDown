"""成功下载计数的本机待上报队列。服务器收到增量后直接累加。"""

import logging
from contextlib import contextmanager
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .api import PaymentError


class UsageReporter:
    def __init__(self, api, device_uuid, path):
        self.api, self.device_uuid, self.path = api, device_uuid, Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        with self._connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS reports "
                               "(report_id TEXT PRIMARY KEY, uuid TEXT NOT NULL, count INTEGER NOT NULL, created_at REAL NOT NULL)")

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(str(self.path), timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def record_video(self):
        report_id = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute("INSERT INTO reports VALUES (?, ?, ?, ?)",
                               (report_id, self.device_uuid, 1, time.time()))
        self._wake.set()
        return report_id

    def flush(self):
        with self._connect() as connection:
            rows = connection.execute("SELECT report_id, uuid, count FROM reports ORDER BY created_at, report_id LIMIT 20").fetchall()
        for report_id, device_uuid, count in rows:
            if self._stop.is_set():
                return False
            try:
                self.api.report_downloads(device_uuid, count)
            except PaymentError:
                return False
            with self._connect() as connection:
                connection.execute("DELETE FROM reports WHERE report_id = ?", (report_id,))
        return len(rows) == 20

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="download-usage-reporter")
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            self._wake.clear()
            try:
                more = self.flush()
            except Exception:
                logging.getLogger(__name__).warning("下载统计暂未上报，保留本机队列等待重试")
                more = False
            if not more:
                self._wake.wait(15)

    def close(self):
        self._stop.set()
        self._wake.set()
