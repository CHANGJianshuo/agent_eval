"""SQLite 轻量索引层 —— 评测 run 历史 + 参数复用。

设计:Python stdlib sqlite3,无 ORM 依赖。schema 极简(一张 runs 表)。
未来升级 Postgres:把 sqlite3 换成 psycopg + 改连接字符串那一行。
"""
from .repo import (
    DEFAULT_DB,
    append_run,
    get_run,
    init_db,
    list_runs,
    migrate_existing_traces,
    update_run,
    delete_run,
)

__all__ = [
    "DEFAULT_DB", "init_db", "append_run", "update_run", "get_run",
    "list_runs", "delete_run", "migrate_existing_traces",
]
