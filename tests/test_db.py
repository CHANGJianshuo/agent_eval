"""SQLite repo 单测。"""
from __future__ import annotations

import json
from pathlib import Path

from claw_eval.db.repo import (
    append_run,
    delete_run,
    get_run,
    init_db,
    list_runs,
    migrate_existing_traces,
    update_run,
)


def test_init_db_creates_table(tmp_path: Path):
    db = tmp_path / "t.db"
    init_db(db)
    assert db.exists()
    # 二次调用幂等
    init_db(db)


def test_append_and_get_run(tmp_path: Path):
    db = tmp_path / "t.db"
    append_run("v1", "t", {"total": 30, "label": "v1"}, db_path=db)
    r = get_run("v1", db_path=db)
    assert r["run_id"] == "v1"
    assert r["task_id"] == "t"
    assert r["status"] == "running"
    assert r["params"] == {"total": 30, "label": "v1"}


def test_update_run(tmp_path: Path):
    db = tmp_path / "t.db"
    append_run("v1", "t", {"total": 30}, db_path=db)
    update_run("v1", db_path=db, status="done", n_results=30, pass_rate=0.5)
    r = get_run("v1", db_path=db)
    assert r["status"] == "done"
    assert r["n_results"] == 30
    assert r["pass_rate"] == 0.5


def test_list_runs_filters_by_task(tmp_path: Path):
    db = tmp_path / "t.db"
    append_run("r1", "a", {}, db_path=db)
    append_run("r2", "b", {}, db_path=db)
    append_run("r3", "a", {}, db_path=db)
    rows = list_runs(task_id="a", db_path=db)
    assert len(rows) == 2
    assert {r["run_id"] for r in rows} == {"r1", "r3"}


def test_list_runs_descending_created(tmp_path: Path):
    import time
    db = tmp_path / "t.db"
    append_run("old", "t", {}, db_path=db)
    time.sleep(0.01)
    append_run("new", "t", {}, db_path=db)
    rows = list_runs("t", db_path=db)
    # 最新的排前
    assert rows[0]["run_id"] == "new"


def test_delete_run(tmp_path: Path):
    db = tmp_path / "t.db"
    append_run("v1", "t", {}, db_path=db)
    delete_run("v1", db_path=db)
    assert get_run("v1", db_path=db) is None


def test_append_overwrites_existing(tmp_path: Path):
    db = tmp_path / "t.db"
    append_run("v1", "t", {"total": 10}, db_path=db)
    append_run("v1", "t", {"total": 50}, db_path=db, agent_version="vX")
    r = get_run("v1", db_path=db)
    assert r["params"]["total"] == 50
    assert r["agent_version"] == "vX"


def test_migrate_existing_traces(tmp_path: Path):
    """从文件系统扫 run_id 子目录入库。"""
    traces = tmp_path / "traces" / "demo_run"
    traces.mkdir(parents=True)
    # 造 3 个 result.json
    for i in range(3):
        (traces / f"x_t{i}.result.json").write_text(json.dumps({
            "task_id": "my_task",
            "task_score": 0.8 if i > 0 else 0.3,
            "passed": i > 0,
        }), encoding="utf-8")

    db = tmp_path / "t.db"
    n = migrate_existing_traces(tmp_path / "traces", db_path=db)
    assert n == 1
    r = get_run("demo_run", db_path=db)
    assert r["task_id"] == "my_task"
    assert r["n_results"] == 3
    assert r["pass_rate"] - (2 / 3) < 0.01
    assert r["status"] == "done"

    # 二次调用幂等
    n2 = migrate_existing_traces(tmp_path / "traces", db_path=db)
    assert n2 == 0


def test_migrate_skips_empty_dirs(tmp_path: Path):
    (tmp_path / "traces" / "empty_run").mkdir(parents=True)
    db = tmp_path / "t.db"
    assert migrate_existing_traces(tmp_path / "traces", db_path=db) == 0
