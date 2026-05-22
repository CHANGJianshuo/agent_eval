"""测试(test = run)相关 endpoints。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from ..db import get_run, list_runs


def _root() -> Path:
    cur = Path(__file__).resolve()
    for p in [cur, *cur.parents]:
        if (p / "pyproject.toml").exists():
            return p
    return Path.cwd()


ROOT = _root()
TASKS_DIR = ROOT / "tasks"
REPORTS_DIR = ROOT / "reports"


router = APIRouter()


class TestInfo(BaseModel):
    test_id: str
    task_id: str
    status: str = "?"
    created_at: str = ""
    agent_version: str | None = None
    params: dict = {}
    n_results: int = 0
    pass_rate: float | None = None
    task_score_avg: float | None = None
    milestones: dict[str, bool] = {}     # m1..m4 for test progress


class NewTestRequest(BaseModel):
    test_id: str = ""                    # 留空则自动生成
    total: int = 30
    no_judge: bool = False
    weights: dict[str, float] = {}       # persona -> weight (legacy)
    dimensions: dict[str, dict[str, float]] = {}   # 5 维度比例 → persona_factory
    auto_recommend: bool = False
    prompt_version: str | None = None    # 用某历史版本


class PreviewRequest(BaseModel):
    dimensions: dict[str, dict[str, float]]
    n: int = 30
    seed: int = 0


class PreviewResult(BaseModel):
    distribution: dict[str, dict[str, int]]    # 每维度实际命中
    samples: list[dict[str, str]]              # 前 N 个样本的 demographics


class JobStatus(BaseModel):
    job_id: str
    status: str
    message: str = ""


def _test_milestones(test: dict, task_id: str) -> dict[str, bool]:
    m1 = True
    m2 = test.get("status") == "done"
    m3 = (REPORTS_DIR / f"task_{task_id}.html").exists()
    m4 = False
    rec = REPORTS_DIR / f"recommendations_{task_id}.json"
    if rec.exists() and test.get("created_at"):
        try:
            test_dt = datetime.fromisoformat(test["created_at"])
            rec_dt = datetime.fromtimestamp(rec.stat().st_mtime)
            m4 = rec_dt >= test_dt
        except Exception:
            m4 = True
    return {"m1": m1, "m2": m2, "m3": m3, "m4": m4}


@router.get("/tasks/{task_id}/tests", response_model=list[TestInfo])
def list_tests_for_task(task_id: str):
    """该任务的所有测试。"""
    runs = list_runs(task_id=task_id)
    return [
        TestInfo(
            test_id=r["run_id"],
            task_id=r["task_id"],
            status=r.get("status", "?"),
            created_at=r.get("created_at", ""),
            agent_version=r.get("agent_version"),
            params=r.get("params", {}),
            n_results=r.get("n_results", 0),
            pass_rate=r.get("pass_rate"),
            task_score_avg=r.get("task_score_avg"),
            milestones=_test_milestones(r, task_id),
        )
        for r in runs
    ]


@router.get("/tests/{test_id}", response_model=TestInfo)
def get_test(test_id: str):
    """单次测试详情。"""
    r = get_run(test_id)
    if not r:
        raise HTTPException(404, f"测试 {test_id} 不在 DB")
    return TestInfo(
        test_id=r["run_id"],
        task_id=r["task_id"],
        status=r.get("status", "?"),
        created_at=r.get("created_at", ""),
        agent_version=r.get("agent_version"),
        params=r.get("params", {}),
        n_results=r.get("n_results", 0),
        pass_rate=r.get("pass_rate"),
        task_score_avg=r.get("task_score_avg"),
        milestones=_test_milestones(r, r["task_id"]),
    )


_TEST_JOBS: dict[str, dict] = {}


@router.post("/tasks/{task_id}/tests", response_model=JobStatus)
def start_test(task_id: str, req: NewTestRequest, background: BackgroundTasks):
    """启动新测试(后台异步跑 batch)。"""
    if not (TASKS_DIR / task_id).exists():
        raise HTTPException(404, f"任务 {task_id} 不存在")

    test_id = req.test_id or f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    job_id = f"test_{test_id}"
    _TEST_JOBS[job_id] = {"status": "running", "task_id": task_id,
                          "test_id": test_id, "log": []}

    def _run_batch():
        try:
            cmd = [sys.executable, "-m", "claw_eval.cli", "batch",
                   "--task", task_id, "--total", str(req.total),
                   "--label", test_id]
            if req.no_judge:
                cmd.append("--no-judge")
            if req.dimensions:
                # 维度模式 → persona_factory(优先)
                cmd += ["--dimensions",
                          json.dumps(req.dimensions, ensure_ascii=False)]
            elif req.weights:
                cmd += ["--weights", json.dumps(req.weights, ensure_ascii=False)]
            env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                     env=env, cwd=str(ROOT))
            _TEST_JOBS[job_id]["log"] = proc.stdout[-3000:]
            if proc.returncode != 0:
                _TEST_JOBS[job_id]["status"] = "failed"
                return
            if req.auto_recommend:
                rp = subprocess.run(
                    [sys.executable, "-m", "claw_eval.cli", "recommend",
                     "--task", task_id],
                    capture_output=True, text=True, env=env, cwd=str(ROOT))
                _TEST_JOBS[job_id]["log"] += "\n--- recommend ---\n" + rp.stdout[-1500:]
            _TEST_JOBS[job_id]["status"] = "done"
        except Exception as exc:
            _TEST_JOBS[job_id]["status"] = "failed"
            _TEST_JOBS[job_id]["log"] = str(exc)

    background.add_task(_run_batch)
    return JobStatus(job_id=job_id, status="running",
                      message=f"测试 {test_id} 已启动")


@router.get("/jobs/test/{job_id}", response_model=JobStatus)
def get_test_job(job_id: str):
    if job_id not in _TEST_JOBS:
        raise HTTPException(404)
    j = _TEST_JOBS[job_id]
    return JobStatus(job_id=job_id, status=j["status"],
                      message=str(j.get("log", ""))[-1500:])


@router.post("/tasks/{task_id}/preview-personas", response_model=PreviewResult)
def preview_personas(task_id: str, req: PreviewRequest):
    """预览:按 dimensions 比例采样 N 次,看实际分布 + 前几个样本。"""
    from ..persona_factory import (
        generate_personas, preview_distribution,
    )
    if not (TASKS_DIR / task_id).exists():
        raise HTTPException(404, f"任务 {task_id} 不存在")
    try:
        dist = preview_distribution(req.dimensions, req.n, seed=req.seed)
        personas = generate_personas(
            req.dimensions, TASKS_DIR / task_id, n=min(req.n, 10),
            seed=req.seed)
        samples = [p.demographics.model_dump() for p in personas]
        return PreviewResult(distribution=dist, samples=samples)
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.get("/tasks/{task_id}/recommendations")
def get_recommendations(task_id: str):
    """读改进建议。"""
    rec_file = REPORTS_DIR / f"recommendations_{task_id}.json"
    if not rec_file.exists():
        return {"recommendations": [], "generated_at": None}
    return json.loads(rec_file.read_text(encoding="utf-8"))
