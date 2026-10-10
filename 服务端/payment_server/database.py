from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import uuid

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine


class Database:
    def __init__(self, url):
        self.engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(self.engine, "connect")
        def configure_sqlite(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=30000")

    def initialize(self):
        # 先保留完整快照，再原地改名和补列，保留用户、订单与授权数据。
        with self.engine.connect() as connection:
            tables, columns = self._schema(connection)
        legacy = tables & {"payment_devices", "payment_orders", "payment_download_reports"}
        missing = columns and not {"last_opened_at", "downloaded_video_count"}.issubset(columns)
        if legacy or missing:
            self._backup_before_migration()
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                tables, columns = self._schema(connection)
                # 只移除已停用的上报去重记录，用户表中的累计数量继续保留。
                if "payment_download_reports" in tables:
                    connection.exec_driver_sql("DROP TABLE payment_download_reports")
                if "payment_devices" in tables:
                    connection.exec_driver_sql("ALTER TABLE payment_devices RENAME TO users")
                if "payment_orders" in tables:
                    connection.exec_driver_sql("ALTER TABLE payment_orders RENAME TO orders")
                if columns:
                    if "last_opened_at" not in columns:
                        connection.exec_driver_sql("ALTER TABLE users ADD COLUMN last_opened_at DATETIME")
                    if "downloaded_video_count" not in columns:
                        connection.exec_driver_sql(
                            "ALTER TABLE users ADD COLUMN downloaded_video_count INTEGER NOT NULL DEFAULT 0 "
                            "CHECK (downloaded_video_count >= 0)"
                        )
                SQLModel.metadata.create_all(connection)
                connection.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS ix_users_last_opened_at ON users(last_opened_at)"
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _schema(connection):
        tables = {row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for old, new in (("payment_devices", "users"), ("payment_orders", "orders")):
            if old in tables and new in tables:
                raise RuntimeError(f"数据库同时包含 {old} 和 {new}，请先核对数据；不会自动合并或覆盖")
        name = "users" if "users" in tables else "payment_devices"
        columns = {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({name})")}
        return tables, columns

    def _backup_before_migration(self):
        name = self.engine.url.database
        if not name or name == ":memory:":
            return
        source = Path(name).resolve()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = source.with_name(f"{source.stem}.schema-backup-{stamp}-{uuid.uuid4().hex[:8]}.db")
        with closing(sqlite3.connect(str(source))) as original, closing(sqlite3.connect(str(backup))) as snapshot:
            original.backup(snapshot)

    @contextmanager
    def write(self):
        # 在读取到期时间之前取得 SQLite 写锁，跨线程/进程防止重复加时。
        # 网络请求放在事务外。
        with Session(self.engine) as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
