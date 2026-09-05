"""runs 表 —— 评测 run 元数据 + 参数,支持复用。

字段:
  run_id          PRIMARY KEY,跟 traces/<run_id>/ 目录名一致
  task_id         任务 id
  label           label(可能等于 run_id,允许为空)
  created_at      ISO 时间
  agent_version   开跑时 task.yaml 对应的版本 label(可空)
  params          JSON 串(total / concurrency / no_judge / personas / noise_overlay / ...)
  status          running / done / partial / failed / canceled
  n_results       完成评分的 case 数
  pass_rate       通过率
  task_score_avg  task_score 平均
  note            备注
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path


def _repo_root() -> Path:
    """猜仓库根目录(.claw_eval.db 放这里)。"""
    cur = Path.cwd()
    for p in [cur, *cur.parents]:
        if (p / "pyproject.toml").exists():
            return p
    return cur


DEFAULT_DB = _repo_root() / ".claw_eval.db"


@contextmanager
def _conn(db_path: Path | None = None):
    db = Path(db_path) if db_path else DEFAULT_DB
    conn = sqlite3.connect(db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db(db_path: Path | None = None) -> None:
    """建表(幂等)。"""
    with _conn(db_path) as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            label TEXT,
            created_at TEXT NOT NULL,
            agent_version TEXT,
            params TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            n_results INTEGER DEFAULT 0,
            pass_rate REAL,
            task_score_avg REAL,
            note TEXT
        )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_runs_task ON runs(task_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at)")


def append_run(run_id: str, task_id: str, params: dict,
               agent_version: str | None = None,
               note: str = "",
               db_path: Path | None = None) -> None:
    """新跑 run 时 insert(status=running)。"""
    init_db(db_path)
    with _conn(db_path) as c:
        c.execute("""
        INSERT INTO runs
        (run_id, task_id, label, created_at, agent_version, params,
         status, note)
        VALUES (?, ?, ?, ?, ?, ?, 'running', ?)
        """, (
            run_id, task_id, params.get("label", run_id),
            datetime.now().isoformat(),  # 含 microsecond,保证排序准确
            agent_version, json.dumps(params, ensure_ascii=False),
            note,
        ))


def update_run(run_id: str, db_path: Path | None = None, **updates) -> None:
    """跑完时 update(status / n_results / pass_rate / task_score_avg / note)。"""
    if not updates:
        return
    allowed = {"status", "n_results", "pass_rate", "task_score_avg", "note"}
    if not updates.keys() <= allowed:
        raise ValueError("Unsupported run update fields")
    cols = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [run_id]
    with _conn(db_path) as c:
        c.execute(f"UPDATE runs SET {cols} WHERE run_id = ?", vals)


def get_run(run_id: str, db_path: Path | None = None) -> dict | None:
    init_db(db_path)
    with _conn(db_path) as c:
        row = c.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["params"] = json.loads(d["params"] or "{}")
        except Exception:
            d["params"] = {}
        return d


def list_runs(task_id: str | None = None,
              limit: int = 200,
              db_path: Path | None = None) -> list[dict]:
    init_db(db_path)
    with _conn(db_path) as c:
        if task_id:
            sql = ("SELECT * FROM runs WHERE task_id = ? "
                   "ORDER BY created_at DESC LIMIT ?")
            args: tuple = (task_id, limit)
        else:
            sql = "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?"
            args = (limit,)
        rows = c.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["params"] = json.loads(d["params"] or "{}")
        except Exception:
            d["params"] = {}
        out.append(d)
    return out


def delete_run(run_id: str, db_path: Path | None = None) -> None:
    with _conn(db_path) as c:
        c.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))


# =================== 一次性迁移 ===================

def migrate_existing_traces(traces_dir: Path | None = None,
                            db_path: Path | None = None) -> int:
    """Import legacy runs and reconcile their metrics once with the current score contract."""
    from ..models.trace import GradingResult
    init_db(db_path)
    tdir = Path(traces_dir) if traces_dir else (_repo_root() / "traces")
    if not tdir.exists():
        return 0
    n_added = 0
    for run_dir in sorted(tdir.iterdir()):
        if not run_dir.is_dir():
            continue
        run_id = run_dir.name
        existing = get_run(run_id, db_path)
        if existing and (existing["params"].get("input_hash") or existing["params"].get("metrics_version") == 2):
            continue
        files = list(run_dir.glob("*.result.json"))
        if not files:
            continue
        results = []
        corrupt = 0
        for path in files:
            try:
                results.append(GradingResult.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception:
                corrupt += 1
        complete = [r for r in results if r.status == "complete"]
        n_done = len(complete)
        status = "done" if n_done == len(files) else "partial" if n_done else "failed"
        pass_rate = sum(r.passed is True for r in complete) / n_done if n_done else None
        score_avg = sum(r.task_score for r in complete) / n_done if n_done else None
        task_ids = sorted({r.task_id for r in results})
        params = {**(existing["params"] if existing else {"migrated_from_filesystem": True}), "metrics_version": 2}
        note = f"按当前评分规则重算：{n_done}/{len(files)} 完整评分；损坏文件 {corrupt}；原始 trace 未改动"
        with _conn(db_path) as c:
            if existing:
                c.execute("UPDATE runs SET params=?,status=?,n_results=?,pass_rate=?,task_score_avg=?,note=? WHERE run_id=?",
                          (json.dumps(params), status, n_done, pass_rate, score_avg, note, run_id))
            else:
                c.execute("INSERT INTO runs (run_id,task_id,label,created_at,params,status,n_results,pass_rate,task_score_avg,note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (run_id, "+".join(task_ids) or "unknown", run_id,
                           datetime.fromtimestamp(min(f.stat().st_mtime for f in files)).isoformat(),
                           json.dumps(params), status, n_done, pass_rate, score_avg, note))
                n_added += 1
    return n_added
