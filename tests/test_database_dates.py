import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "服务端"))

from check_database_dates import check_dates


class DatabaseDateTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "dates.db"

    def create_table(self, table="users"):
        with sqlite3.connect(self.path) as connection:
            connection.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, expires_at DATETIME, "
                               "paid_at DATETIME, device_id TEXT, token_hash TEXT)")
            connection.execute(f"INSERT INTO {table} VALUES (1, '2026-02-30 00:00:00', NULL, "
                               "'private-uuid', 'private-token-hash')")
            connection.execute(f"INSERT INTO {table} VALUES (2, '2026-10-09 15:30:00.123456', "
                               "'2024-02-29 00:00:00', 'another-uuid', 'another-token')")

    def test_invalid_calendar_date_detected_without_touching_records_or_private_fields(self):
        self.create_table()
        original = self.path.read_bytes()
        self.assertEqual(check_dates(self.path), [
            {"table": "users", "id": 1, "field": "expires_at", "value": "2026-02-30 00:00:00"}])
        self.assertEqual(self.path.read_bytes(), original)

    def test_legacy_table_and_invalid_order_date_are_checked(self):
        self.create_table("payment_devices")
        with sqlite3.connect(self.path) as connection:
            connection.execute("CREATE TABLE orders (order_id TEXT PRIMARY KEY, created_at DATETIME)")
            connection.execute("INSERT INTO orders VALUES ('test-order', '2026-06-31 10:00:00')")
        errors = check_dates(self.path)
        self.assertEqual([(error["table"], error["field"]) for error in errors],
                         [("payment_devices", "expires_at"), ("orders", "created_at")])

    def test_wrong_path_never_creates_empty_database(self):
        with self.assertRaises(sqlite3.OperationalError):
            check_dates(self.path)
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
