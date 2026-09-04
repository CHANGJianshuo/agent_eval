"""测试(test = run)相关 endpoints。"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from ..db import get_run, list_runs
from ..models.rubric import load_rubrics
from ..models.task import TaskDefinition


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
    total: int = Field(default=30, ge=1, le=500)
    no_judge: bool = False
    weights: dict[str, float] = Field(default_factory=dict)  # persona -> weight (legacy)
    dimensions: dict[str, dict[str, float]] = Field(default_factory=dict)  # 5 维度比例 → persona_factory
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
_SAFE_TEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


@router.post("/tasks/{task_id}/tests", response_model=JobStatus)
def start_test(task_id: str, req: NewTestRequest, background: BackgroundTasks):
    """启动新测试(后台异步跑 batch)。"""
    if not (TASKS_DIR / task_id).exists():
        raise HTTPException(404, f"任务 {task_id} 不存在")

    td = TASKS_DIR / task_id
    draft_r = td / "rubrics.draft.yaml"
    final_r = td / "rubrics.yaml"
    draft_p = td / "personas_draft"

    # ── 配置预检 ──
    errors = []
    task_file = td / "task.yaml"
    if not task_file.exists():
        errors.append("缺少 task.yaml（任务 Prompt）")
    else:
        try:
            TaskDefinition.from_yaml(task_file).rendered_prompt()
        except Exception as exc:
            errors.append(f"task.yaml 校验失败: {exc}")
    if not final_r.exists():
        if draft_r.exists():
            errors.append("评分项仍是草稿，请先在任务页审核并转正")
        else:
            errors.append("缺少评分项（rubrics.yaml 不存在）")
    else:
        try:
            rubrics = load_rubrics(final_r)
            if not rubrics:
                errors.append("rubrics.yaml 内容为空")
        except Exception as e:
            errors.append(f"rubrics.yaml 校验失败: {e}")
    scripts_dir = td / "personas"
    n_scripts = len(list(scripts_dir.glob("*.yaml"))) if scripts_dir.exists() else 0
    if n_scripts == 0:
        has_drafts = draft_p.exists() and any(draft_p.glob("*.yaml"))
        if has_drafts:
            errors.append("模拟用户剧本仍是草稿，请先在任务页审核并转正")
        else:
            errors.append("缺少剧本（personas/ 目录为空）")
    if not (td / "grader.py").exists():
        errors.append("缺少评分器（grader.py）")
    if req.prompt_version is not None:
        errors.append("prompt_version 尚不支持按次运行，请先切换任务版本后再启动")
    if errors:
        raise HTTPException(422, "配置预检失败:\n" + "\n".join(f"• {e}" for e in errors))

    test_id = req.test_id or f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    if not _SAFE_TEST_ID.fullmatch(test_id):
        raise HTTPException(
            422,
            "test_id 只能包含英文字母、数字、下划线和连字符，且必须以字母或数字开头",
        )
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
            combined_log = "\n".join(
                part for part in (proc.stdout, proc.stderr) if part
            )
            _TEST_JOBS[job_id]["log"] = combined_log[-3000:]
            if proc.returncode != 0:
                _TEST_JOBS[job_id]["status"] = "failed"
                return
            # 为本次 run 单独生成报告
            try:
                from ..report.builder import build_dashboard_from_dir
                run_traces = ROOT / "traces" / test_id
                run_report = REPORTS_DIR / test_id
                run_report.mkdir(parents=True, exist_ok=True)
                build_dashboard_from_dir(run_traces, run_report)
            except Exception:
                pass
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


@router.get("/tests/{test_id}/results")
def get_test_results(test_id: str):
    """读取测试的全部 case 结果,用于展示分布和明细。"""
    traces_dir = ROOT / "traces" / test_id
    if not traces_dir.exists():
        return {"results": [], "heatmap": [], "scripts": [], "attitudes": []}

    results = []
    for rf in sorted(traces_dir.glob("*.result.json")):
        try:
            d = json.loads(rf.read_text(encoding="utf-8"))
            results.append(d)
        except Exception:
            continue

    # 构建 script × attitude 矩阵
    cells: dict[tuple[str, str], list[float]] = {}
    all_scripts: set[str] = set()
    all_attitudes: set[str] = set()
    for r in results:
        sid = r.get("script_id") or r.get("persona_id", "unknown")
        demo = r.get("demographics", {})
        att = demo.get("attitude", "unknown")
        all_scripts.add(sid)
        all_attitudes.add(att)
        cells.setdefault((sid, att), []).append(r.get("task_score", 0))

    scripts = sorted(all_scripts)
    attitudes = sorted(all_attitudes)
    heatmap = []
    for sid in scripts:
        for att in attitudes:
            scores = cells.get((sid, att), [])
            if scores:
                heatmap.append({
                    "script": sid,
                    "attitude": att,
                    "count": len(scores),
                    "avg_score": round(sum(scores) / len(scores), 3),
                    "passed": sum(1 for s in scores if s >= 0.6),
                })

    return {
        "results": results,
        "scripts": scripts,
        "attitudes": attitudes,
        "heatmap": heatmap,
    }


@router.post("/tests/{test_id}/report")
def generate_test_report(test_id: str, background: BackgroundTasks):
    """为单次测试生成 HTML 报告(只读该 run 的 traces）。"""
    traces_dir = ROOT / "traces" / test_id
    if not traces_dir.exists():
        raise HTTPException(404, f"traces/{test_id} 不存在")

    n_results = len(list(traces_dir.glob("*.result.json")))
    if n_results == 0:
        raise HTTPException(422, "该测试没有评分结果（.result.json），无法生成报告")

    run_report_dir = REPORTS_DIR / test_id
    job_key = f"report_{test_id}"
    _TEST_JOBS[job_key] = {"status": "running", "task_id": "", "test_id": test_id, "log": []}

    def _build():
        try:
            from ..report.builder import build_dashboard_from_dir
            run_report_dir.mkdir(parents=True, exist_ok=True)
            build_dashboard_from_dir(traces_dir, run_report_dir)
            _TEST_JOBS[job_key]["status"] = "done"
        except Exception as exc:
            _TEST_JOBS[job_key]["status"] = "failed"
            _TEST_JOBS[job_key]["log"] = str(exc)

    background.add_task(_build)
    return {"status": "generating", "report_dir": str(run_report_dir)}


@router.get("/tests/{test_id}/report-status")
def get_test_report_status(test_id: str):
    """检查单次测试的报告是否存在。"""
    run_report_dir = REPORTS_DIR / test_id
    # 按 run 目录查看是否有 task 页面
    pages = sorted(run_report_dir.glob("task_*.html")) if run_report_dir.exists() else []
    if pages:
        return {"exists": True, "url": f"/reports/{test_id}/{pages[0].name}"}

    # 后备：全局报告
    r = get_run(test_id)
    if r:
        global_page = REPORTS_DIR / f"task_{r['task_id']}.html"
        if global_page.exists():
            return {"exists": True, "url": f"/reports/task_{r['task_id']}.html"}

    return {"exists": False, "url": None}


@router.get("/tasks/{task_id}/recommendations")
def get_recommendations(task_id: str):
    """读改进建议。"""
    rec_file = REPORTS_DIR / f"recommendations_{task_id}.json"
    if not rec_file.exists():
        return {"recommendations": [], "generated_at": None}
    return json.loads(rec_file.read_text(encoding="utf-8"))
