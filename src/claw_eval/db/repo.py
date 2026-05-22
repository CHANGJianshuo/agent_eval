"""runs 表 —— 评测 run 元数据 + 参数,支持复用。

字段:
  run_id          PRIMARY KEY,跟 traces/<run_id>/ 目录名一致
  task_id         任务 id
  label           label(可能等于 run_id,允许为空)
  created_at      ISO 时间
  agent_version   开跑时 task.yaml 对应的版本 label(可空)
  params          JSON 串(total / concurrency / no_judge / personas / noise_overlay / ...)
  status          running / done / failed / canceled
  n_results       完成评分的 case 数
  pass_rate       通过率
  task_score_avg  task_score 平均
  note            备注
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path


def _repo_root() -> Path:
    """猜仓库根目录(.claw_eval.db 放这里)。"""
    cur = Path.cwd()
    for p in [cur, *cur.parents]:
        if (p / "pyproject.toml").exists():
            return p
    return cur


DEFAULT_DB = _repo_root() / ".claw_eval.db"


def _conn(db_path: Path | None = None) -> sqlite3.Connection:
    db = Path(db_path) if db_path else DEFAULT_DB
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


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
        INSERT OR REPLACE INTO runs
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
    """扫 traces/ 下既有 run_id 子目录,populate DB(幂等)。

    返回:新写入的 run 数(已存在的不会重新统计)。
    """
    init_db(db_path)
    tdir = Path(traces_dir) if traces_dir else (_repo_root() / "traces")
    if not tdir.exists():
        return 0

    n_added = 0
    for run_dir in tdir.iterdir():
        if not run_dir.is_dir():
            continue
        run_id = run_dir.name
        # 已有就跳过
        if get_run(run_id, db_path):
            continue
        result_files = list(run_dir.glob("*.result.json"))
        if not result_files:
            continue
        # 推 task_id + 算指标
        task_ids = set()
        passes = 0
        score_sum = 0.0
        n_done = 0
        for rf in result_files:
            try:
                d = json.loads(rf.read_text(encoding="utf-8"))
                if d.get("task_id"):
                    task_ids.add(d["task_id"])
                if d.get("passed"):
                    passes += 1
                score_sum += float(d.get("task_score") or 0)
                n_done += 1
            except Exception:
                pass
        task_id = list(task_ids)[0] if len(task_ids) == 1 else (
            "+".join(sorted(task_ids)) if task_ids else "unknown")
        pass_rate = passes / n_done if n_done else 0.0
        score_avg = score_sum / n_done if n_done else 0.0
        created_at = datetime.fromtimestamp(
            min(rf.stat().st_mtime for rf in result_files)
        ).isoformat(timespec="seconds")

        with _conn(db_path) as c:
            c.execute("""
            INSERT INTO runs
            (run_id, task_id, label, created_at, params, status,
             n_results, pass_rate, task_score_avg, note)
            VALUES (?, ?, ?, ?, ?, 'done', ?, ?, ?, ?)
            """, (
                run_id, task_id, run_id, created_at,
                json.dumps({"migrated_from_filesystem": True}),
                n_done, pass_rate, score_avg,
                "filesystem migration",
            ))
        n_added += 1
    return n_added
