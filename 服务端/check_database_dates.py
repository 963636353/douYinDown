"""只读检查 SQLite 时间字段，不显示设备令牌或自动修改授权。"""

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path


TABLES = {
    "users": "id", "payment_devices": "id",
    "orders": "order_id", "payment_orders": "order_id",
}


def check_dates(path):
    path = Path(path).resolve()
    # mode=ro 保证检查不会创建空数据库或修改已有记录。
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        errors = []
        for table, primary in TABLES.items():
            if table not in tables:
                continue
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')
                       if row[2].upper() in {"DATETIME", "TIMESTAMP", "DATE"}]
            if not columns:
                continue
            selected = ", ".join('"' + column.replace('"', '""') + '"' for column in [primary, *columns])
            for row in connection.execute(f'SELECT {selected} FROM "{table}"'):
                for column, value in zip(columns, row[1:]):
                    if value is None:
                        continue
                    try:
                        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        errors.append({"table": table, "id": row[0], "field": column, "value": value})
        return errors
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description="只读检查用户及订单表中不合法的日期")
    parser.add_argument("--database", help="SQLite 数据库文件路径；默认读取服务端配置")
    args = parser.parse_args()
    if args.database:
        path = args.database
    else:
        from sqlalchemy.engine import make_url
        from payment_server.config import Settings
        path = make_url(Settings.from_env().database_url).database
    try:
        errors = check_dates(path)
    except (OSError, sqlite3.Error) as exc:
        print(f"无法检查数据库：{exc}。请确认文件路径；检查不会新建数据库。")
        return 2
    for error in errors:
        print(f"异常日期：表={error['table']}，ID={error['id']}，字段={error['field']}，原值={error['value']!r}")
    print(f"检查完成：发现 {len(errors)} 个异常日期。数据库未作任何修改。")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
