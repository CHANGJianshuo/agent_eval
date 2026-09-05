"""Durable local jobs shared by generation, evaluation, and reports.

The web process owns its workers. A dead owner is reported as interrupted;
restarting never silently repeats paid model calls. SQLite supports concurrent readers.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
from datetime import datetime, timezone

from ..db.repo import _conn, init_db, update_run

ACTIVE = {"running", "canceling"}


def _token(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return ""


def _alive(pid: int, token: str) -> bool:
    try:
        os.kill(pid, 0)
        return not token or token == _token(pid)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _init():
    init_db()
    with _conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, data TEXT NOT NULL)")


def create(job_id: str, **data) -> dict:
    _init()
    job = {"job_id": job_id, "status": "running", "log": "", "step": 0,
           "total_steps": 0, "step_label": "", "task_id": "", "test_id": "",
           "job_type": "test", "created_at": datetime.now(timezone.utc).isoformat(),
           "owner_pid": os.getpid(), "owner_token": _token(os.getpid()), **data}
    with _conn() as c:
        c.execute("INSERT INTO jobs VALUES (?, ?)", (job_id, json.dumps(job)))
    return job


def update(job_id: str, **fields) -> None:
    _init()
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT data FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            raise KeyError(job_id)
        job = json.loads(row[0])
        if job["status"] in {"canceled", "interrupted"}:
            return
        job.update(fields)
        c.execute("UPDATE jobs SET data = ? WHERE job_id = ?", (json.dumps(job), job_id))


def _recover(job: dict) -> dict:
    if job["status"] in ACTIVE and not _alive(job["owner_pid"], job["owner_token"]):
        pid = job.get("worker_pid")
        if pid and _alive(pid, job.get("worker_token", "")):
            _stop(pid)
        update(job["job_id"], status="interrupted", log="服务进程已退出，任务中断；已保存的结果仍可查看。请使用新的运行 ID 重跑。")
        job.update(status="interrupted", log="服务进程已退出，任务中断；已保存的结果仍可查看。")
        if job.get("test_id") and job["job_type"] == "test":
            from ..db import get_run
            run = get_run(job["test_id"])
            if run and run["status"] in {"prepared", "running"}:
                update_run(job["test_id"], status="interrupted", note=job["log"])
    return job


def get(job_id: str) -> dict | None:
    _init()
    with _conn() as c:
        row = c.execute("SELECT data FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    return _recover(json.loads(row[0])) if row else None


def list_all() -> list[dict]:
    _init()
    with _conn() as c:
        rows = c.execute("SELECT data FROM jobs ORDER BY rowid DESC LIMIT 500").fetchall()
    return [_recover(json.loads(row[0])) for row in rows]


def _stop(pid: int):
    try:
        os.killpg(pid, signal.SIGTERM)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and _alive(pid, ""):
            time.sleep(0.05)
        if _alive(pid, ""):
            os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _tail(fd: int) -> str:
    # pread leaves the subprocess's shared file offset untouched.
    size = os.fstat(fd).st_size
    return os.pread(fd, min(size, 16000), max(0, size - 16000)).decode("utf-8", errors="replace")


def run_process(job_id: str, cmd: list[str], *, env: dict, cwd: str,
                timeout: float = 7200, markers: dict | None = None) -> subprocess.CompletedProcess:
    """Bounded subprocess, bounded log tail, observable progress and cancellation."""
    if get(job_id)["status"] != "running":
        update(job_id, status="canceled")
        raise RuntimeError("任务已取消")
    with tempfile.TemporaryFile(mode="w+b") as output:
        proc = subprocess.Popen(cmd, stdout=output, stderr=subprocess.STDOUT,
                                text=True, env=env, cwd=cwd, start_new_session=True)
        update(job_id, worker_pid=proc.pid, worker_token=_token(proc.pid))
        start = time.monotonic()
        try:
            while proc.poll() is None:
                job = get(job_id)
                if job["status"] == "canceling":
                    update(job_id, status="canceled", log="用户取消了任务")
                    raise RuntimeError("任务已取消")
                if time.monotonic() - start > timeout:
                    raise TimeoutError(f"任务超过 {timeout:g} 秒上限")
                log = _tail(output.fileno())
                progress = {}
                for marker, (step, label) in (markers or {}).items():
                    if marker in log:
                        progress = {"step": step, "step_label": label}
                update(job_id, log=log, **progress)
                time.sleep(0.25)
            log = _tail(output.fileno())
            update(job_id, log=log)
            return subprocess.CompletedProcess(cmd, proc.returncode, log, "")
        finally:
            if proc.poll() is None:
                _stop(proc.pid)
            proc.wait()
