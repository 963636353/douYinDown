"""用户统计、数据库升级和身份迁移，使用临时数据库，不访问商户服务。"""

import json
import sqlite3
import sys
import tempfile
import unittest
import uuid
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from fastapi.testclient import TestClient
from sqlmodel import Session, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "服务端"))

from payment_server.app import create_app
from payment_server.config import Settings
from payment_server.database import Database
from payment_server.models import Device, Order
from payment_server.service import token_hash, utcnow
from payment_kit import PaymentAPI, PaymentConfig, PaymentError
from payment_kit.identity import DeviceIdentity
from payment_kit.usage import UsageReporter


def hardware_id(name="computer-one"):
    return uuid.uuid5(uuid.NAMESPACE_URL, name).hex


class UserStatsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.gateway = Mock()
        self.gateway.create.return_value = "weixin://test"
        self.gateway.image.return_value = "data:image/png;base64,dGVzdA=="
        self.gateway.query.side_effect = lambda order: {"out_trade_no": order.order_id, "trade_state": "NOTPAY"}
        self.app = create_app(Settings(database_url=f"sqlite:///{self.path.as_posix()}/test.db"), self.gateway)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.service = self.app.state.payments
        self.old_id = uuid.uuid4().hex
        self.token = self.client.post("/api/pc/register", json={"deviceId": self.old_id}).json()["deviceToken"]
        self.headers = {"Authorization": "Bearer " + self.token}
        self.uuid = hardware_id()

    def startup(self, computer=None):
        return self.client.post("/api/pc/startup", json={"uuid": computer or self.uuid}, headers=self.headers)

    def device(self):
        with Session(self.service.db.engine) as session:
            return session.exec(select(Device).where(Device.token_hash == token_hash(self.token))).one()

    def report(self, count=1, computer=None):
        return self.client.post("/api/pc/download-count", headers=self.headers,
                                json={"uuid": computer or self.uuid, "count": count})

    def test_startup_preserves_id_dates_paid_and_pending_orders(self):
        before = self.device()
        orders = []
        for plan in ("plan_30d", "plan_365d"):
            orders.append(self.client.post("/api/pc/create-order", headers=self.headers,
                          json={"deviceId": self.old_id, "planId": plan}).json()["orderId"])
        self.service.settle({"return_code": "SUCCESS", "result_code": "SUCCESS", "out_trade_no": orders[0],
                             "total_fee": "1000", "transaction_id": "test-transaction"})
        paid = self.device()
        response = self.startup()
        self.assertEqual(response.status_code, 200, response.text)
        current = self.device()
        self.assertEqual((current.id, current.first_seen, current.expires_at, current.paid_at),
                         (before.id, before.first_seen, paid.expires_at, paid.paid_at))
        with Session(self.service.db.engine) as session:
            self.assertEqual([session.get(Order, key).device_id for key in orders], [self.uuid, self.uuid])
            self.assertEqual([session.get(Order, key).status for key in orders], ["SUCCESS", "PENDING"])
        self.assertEqual(self.client.get("/api/pc/order-status/" + orders[0], headers=self.headers).json()["status"], "SUCCESS")
        self.assertTrue(self.client.get("/api/pc/license/" + self.uuid, headers=self.headers).json()["isPaid"])
        self.assertEqual(self.client.get("/api/pc/stats").json()["total_users"], 1)
        self.assertEqual(self.startup().status_code, 200)
        self.assertEqual(self.device().expires_at, paid.expires_at)

    def test_existing_uuid_collision_and_bound_uuid_change_do_not_merge(self):
        self.client.post("/api/pc/register", json={"deviceId": self.uuid})
        self.assertEqual(self.startup().status_code, 409)
        self.assertEqual(self.device().device_id, self.old_id)
        alternate = hardware_id("different-computer")
        self.assertEqual(self.startup(alternate).status_code, 200)
        self.assertEqual(self.startup(hardware_id("third-computer")).status_code, 409)
        self.assertEqual(self.device().device_id, alternate)

    def test_license_polling_downloads_and_registration_do_not_update_open_time(self):
        first = datetime(2026, 10, 8, 1)
        with patch("payment_server.service.utcnow", return_value=first):
            self.assertEqual(self.startup().status_code, 200)
        original_first_seen = self.device().first_seen
        with patch("payment_server.service.utcnow", return_value=first + timedelta(hours=2)):
            self.client.get("/api/pc/license/" + self.uuid, headers=self.headers)
            self.report(4)
            self.client.post("/api/pc/register", json={"deviceId": self.uuid, "deviceToken": self.token})
        self.assertEqual(self.device().last_opened_at, first)
        self.assertEqual(self.device().first_seen, original_first_seen)

    def test_report_validation_ownership_and_direct_accumulation(self):
        self.startup()
        self.assertEqual(self.report(3).json()["downloaded_video_count"], 3)
        self.assertEqual(self.report(3).json()["downloaded_video_count"], 6)
        self.assertEqual(self.report(4).json()["downloaded_video_count"], 10)
        self.assertEqual(self.report(1, computer=hardware_id("someone-else")).status_code, 403)
        for value in (0, -1, 1.2, True, "1", 1000001):
            self.assertEqual(self.report(value).status_code, 422)
        self.assertEqual(self.client.post("/api/pc/download-count", json={"uuid": self.uuid, "count": 1}).status_code, 401)
        self.assertEqual(self.device().downloaded_video_count, 10)

    def test_concurrent_download_reports_accumulate_without_loss(self):
        self.startup()
        with ThreadPoolExecutor(max_workers=6) as executor:
            list(executor.map(lambda _: self.service.record_downloads(self.uuid, self.uuid, 2), range(12)))
            list(executor.map(lambda _: self.service.record_downloads(self.uuid, self.uuid, 1), range(12)))
        self.assertEqual(self.device().downloaded_video_count, 36)

    def test_old_client_report_id_is_ignored_and_only_two_tables_exist(self):
        self.startup()
        payload = {"uuid": self.uuid, "count": 2, "report_id": uuid.uuid4().hex}
        for expected in (2, 4):
            response = self.client.post("/api/pc/download-count", headers=self.headers, json=payload)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["downloaded_video_count"], expected)
        with self.service.db.engine.connect() as connection:
            tables = {row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertEqual(tables, {"users", "orders"})

    def test_stats_natural_day_boundaries_exclude_future_and_unreported_launches(self):
        now = datetime(2026, 10, 8, 12, 30)
        today = datetime(2026, 10, 7, 16)
        week = today - timedelta(days=6)
        month = today - timedelta(days=29)
        dates = [today, today - timedelta(microseconds=1), week, week - timedelta(microseconds=1),
                 month, month - timedelta(microseconds=1), now + timedelta(seconds=1)]
        with self.service.db.write() as session:
            for index, opened in enumerate(dates):
                session.add(Device(device_id=hardware_id(str(index)), token_hash=token_hash(str(index)),
                                   first_seen=month, last_seen=now, last_opened_at=opened, expires_at=now))
        with patch("payment_server.service.utcnow", return_value=now):
            response = self.client.get("/api/pc/stats")
        data = response.json()
        self.assertEqual((data["today_active"], data["active_7d"], data["active_30d"], data["total_users"]), (1, 3, 5, 8))
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(set(data), {"today_active", "active_7d", "active_30d", "total_users", "as_of", "timezone"})
        page = self.client.get("/stats")
        self.assertEqual(page.status_code, 200)
        for key in ("today_active", "active_7d", "active_30d", "total_users"):
            self.assertIn(f'id="{key}"', page.text)
        self.assertNotIn(self.token, page.text)
        self.assertNotIn(self.old_id, page.text)

    def test_missing_credentials_cannot_reset_trial_on_same_computer(self):
        self.startup()
        expiry = self.device().expires_at
        response = self.client.post("/api/pc/register", json={"deviceId": self.uuid})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.device().expires_at, expiry)

    def test_client_recovers_migration_with_lost_response_and_no_new_trial(self):
        config = PaymentConfig(api_base="http://testserver/api/pc")
        legacy = self.path / "software" / "identity.json"
        legacy.parent.mkdir()
        legacy.write_text(json.dumps({"apiBase": config.api_base, "deviceId": self.old_id, "deviceToken": self.token}))
        path = self.path / "profile" / "identity.json"
        identity = DeviceIdentity(path, config.api_base, device_id=self.uuid, legacy_path=legacy)
        api = PaymentAPI(config, identity)
        calls = []
        lose = True

        def bridge(method, url, **kwargs):
            nonlocal lose
            calls.append(url)
            kwargs.pop("timeout")
            self.assertEqual(kwargs["headers"]["X-Device-UUID"], self.uuid)
            result = self.client.request(method, url.removeprefix("http://testserver"), **kwargs)
            if url.endswith("/startup") and lose:
                lose = False
                raise requests.Timeout("simulated lost response")
            response = requests.Response()
            response.status_code, response._content, response.url = result.status_code, result.content, url
            return response

        before = self.device()
        with patch("payment_kit.api.requests.request", side_effect=bridge):
            with self.assertRaises(PaymentError):
                api.startup()
            restored = DeviceIdentity(path, config.api_base, device_id=self.uuid)
            api = PaymentAPI(config, restored)
            api.startup()
            api.get_license(self.old_id)
            api.get_license(self.old_id)
            # 再次启动上报只发生一次；日常查询不重复上报。
            self.assertEqual(sum(url.endswith("/startup") for url in calls), 2)
        self.assertEqual(self.device().id, before.id)
        self.assertEqual(self.device().first_seen, before.first_seen)
        self.assertEqual(self.device().expires_at, before.expires_at)
        self.assertEqual(restored.device_id, self.uuid)
        self.assertEqual(json.loads(path.read_text())["deviceId"], self.uuid)
        legacy.unlink()
        self.assertEqual(DeviceIdentity(path, config.api_base, device_id=self.uuid).token, self.token)

    def test_outbox_preserves_failed_increment_and_retries_after_restart(self):
        api = Mock()
        api.report_downloads.side_effect = PaymentError("offline", kind="network")
        reporter = UsageReporter(api, self.uuid, self.path / "outbox.db")
        reporter.record_video()
        reporter.flush()
        api.report_downloads.assert_called_once_with(self.uuid, 1)
        reporter.close()
        restored = UsageReporter(api, self.uuid, self.path / "outbox.db")
        api.report_downloads.side_effect = None
        restored.flush()
        self.assertEqual(api.report_downloads.call_count, 2)
        restored.flush()
        self.assertEqual(api.report_downloads.call_count, 2)
        restored.close()


class MigrationTests(unittest.TestCase):
    @staticmethod
    def seed_old_database(path):
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.executescript("""
                CREATE TABLE payment_devices (id INTEGER PRIMARY KEY, device_id VARCHAR NOT NULL UNIQUE,
                    token_hash VARCHAR NOT NULL UNIQUE, first_seen DATETIME NOT NULL, last_seen DATETIME NOT NULL,
                    expires_at DATETIME NOT NULL, paid_at DATETIME, blocked BOOLEAN NOT NULL);
                CREATE TABLE payment_orders (order_id VARCHAR PRIMARY KEY, device_id VARCHAR NOT NULL, plan_id VARCHAR NOT NULL,
                    amount_cents INTEGER NOT NULL, grant_days INTEGER NOT NULL, status VARCHAR NOT NULL,
                    created_at DATETIME NOT NULL, expires_at DATETIME NOT NULL, code_url VARCHAR, last_checked_at DATETIME,
                    transaction_id VARCHAR UNIQUE, paid_at DATETIME, granted_until DATETIME);
                INSERT INTO payment_devices VALUES (9, 'legacy-user', 'hash', '2026-01-01', '2026-10-01', '2027-01-01', NULL, 0);
                INSERT INTO payment_orders (order_id, device_id, plan_id, amount_cents, grant_days, status, created_at, expires_at)
                    VALUES ('old-order', 'legacy-user', 'plan_30d', 1, 1, 'PENDING', '2026-10-01', '2026-10-02');
            """)

    def test_old_database_backup_preserves_rows_orders_and_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "old.db"
            self.seed_old_database(path)
            database = Database(f"sqlite:///{path.as_posix()}")
            try:
                database.initialize()
                database.initialize()
                snapshots = list(path.parent.glob("old.schema-backup-*.db"))
                self.assertEqual(len(snapshots), 1)
                with database.engine.connect() as connection:
                    tables = {row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'")}
                self.assertEqual(tables, {"users", "orders"})
                with Session(database.engine) as session:
                    device = session.get(Device, 9)
                    self.assertEqual(device.device_id, "legacy-user")
                    self.assertIsNone(device.last_opened_at)
                    self.assertEqual(device.downloaded_video_count, 0)
                    self.assertEqual(session.get(Order, "old-order").amount_cents, 1)
                snapshot = sqlite3.connect(snapshots[0])
                try:
                    self.assertNotIn("last_opened_at", {row[1] for row in snapshot.execute("PRAGMA table_info(payment_devices)")})
                    self.assertEqual(snapshot.execute("SELECT id FROM payment_devices").fetchone()[0], 9)
                finally:
                    snapshot.close()
            finally:
                database.engine.dispose()

    def test_three_table_upgrade_preserves_counts_paid_orders_and_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "old-stats.db"
            self.seed_old_database(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.executescript("""
                    ALTER TABLE payment_devices ADD COLUMN last_opened_at DATETIME;
                    ALTER TABLE payment_devices ADD COLUMN downloaded_video_count INTEGER NOT NULL DEFAULT 0;
                    UPDATE payment_devices SET last_opened_at = '2026-10-08 01:00:00', downloaded_video_count = 11,
                        paid_at = '2026-10-01 01:00:00', expires_at = '2027-01-01 01:00:00';
                    CREATE TABLE payment_download_reports (device_pk INTEGER NOT NULL REFERENCES payment_devices(id),
                        report_id VARCHAR NOT NULL, count INTEGER NOT NULL, created_at DATETIME NOT NULL,
                        PRIMARY KEY (device_pk, report_id));
                    INSERT INTO payment_download_reports VALUES (9, 'old-report', 5, '2026-10-01');
                    UPDATE payment_orders SET status = 'SUCCESS', transaction_id = 'old-transaction',
                        paid_at = '2026-10-01 01:00:00', granted_until = '2027-01-01 01:00:00';
                """)
                original_user = connection.execute("SELECT * FROM payment_devices").fetchone()
                original_order = connection.execute("SELECT * FROM payment_orders").fetchone()
            database = Database(f"sqlite:///{path.as_posix()}")
            try:
                database.initialize()
                database.initialize()
                with closing(sqlite3.connect(path)) as connection, connection:
                    self.assertEqual(connection.execute("SELECT * FROM users").fetchone(), original_user)
                    self.assertEqual(connection.execute("SELECT * FROM orders").fetchone(), original_order)
                    self.assertEqual({row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")},
                                     {"users", "orders"})
                snapshots = list(path.parent.glob("old-stats.schema-backup-*.db"))
                self.assertEqual(len(snapshots), 1)
                with closing(sqlite3.connect(snapshots[0])) as snapshot:
                    self.assertEqual(snapshot.execute("SELECT * FROM payment_devices").fetchone(), original_user)
                    self.assertEqual(snapshot.execute("SELECT * FROM payment_orders").fetchone(), original_order)
                    self.assertEqual(snapshot.execute("SELECT count FROM payment_download_reports").fetchone()[0], 5)
            finally:
                database.engine.dispose()

    def test_conflicting_table_names_are_rejected_without_modifying_data(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "conflict.db"
            self.seed_old_database(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
            database = Database(f"sqlite:///{path.as_posix()}")
            try:
                with self.assertRaisesRegex(RuntimeError, "不会自动合并或覆盖"):
                    database.initialize()
                with closing(sqlite3.connect(path)) as connection, connection:
                    self.assertEqual(connection.execute("SELECT id FROM payment_devices").fetchone()[0], 9)
                    self.assertEqual(connection.execute("SELECT status FROM payment_orders").fetchone()[0], "PENDING")
                    self.assertEqual(connection.execute("SELECT count(*) FROM users").fetchone()[0], 0)
            finally:
                database.engine.dispose()


if __name__ == "__main__":
    unittest.main()
