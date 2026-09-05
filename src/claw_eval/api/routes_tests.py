"""测试(test = run)相关 endpoints。"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from . import jobs
from ..runs import prepare_run, load_manifest, validate_id
from ..models.trace import GradingResult

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
    seed: int = Field(default=0, ge=0, le=4294967295)
    weights: dict[str, float] = Field(default_factory=dict)  # persona -> weight (legacy)
    dimensions: dict[str, dict[str, float]] = Field(default_factory=dict)  # 5 维度比例 → persona_factory
    auto_recommend: bool = False
    prompt_version: str | None = None    # 用某历史版本


class PreviewRequest(BaseModel):
    dimensions: dict[str, dict[str, float]]
    n: int = Field(default=30, ge=1, le=500)
    seed: int = Field(default=0, ge=0, le=4294967295)


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
    run_report = REPORTS_DIR / test["run_id"]
    from ..report.builder import report_is_current
    m3 = report_is_current(run_report, task_id)
    from ..report.recommend import recommendations_complete
    m4 = recommendations_complete(run_report / f"recommendations_{task_id}.json")
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


_SAFE_TEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


@router.post("/tasks/{task_id}/tests", response_model=JobStatus)
def start_test(task_id: str, req: NewTestRequest, background: BackgroundTasks):
    """启动新测试(后台异步跑 batch)。"""
    from ..persona_factory import validate_dimensions
    try:
        validate_dimensions(req.dimensions)
        if req.weights:
            from ..sampling import allocate
            allocate(req.weights, req.total)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    try:
        validate_id(task_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
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
    if not errors:
        from ..validator import require_valid_task
        try:
            require_valid_task(td, root=ROOT)
        except ValueError as exc:
            errors.append(str(exc))
    if req.prompt_version is not None:
        errors.append("prompt_version 尚不支持按次运行，请先切换任务版本后再启动")
    if errors:
        raise HTTPException(422, "配置预检失败:\n" + "\n".join(f"• {e}" for e in errors))

    test_id = req.test_id or f"test_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    if not _SAFE_TEST_ID.fullmatch(test_id):
        raise HTTPException(
            422,
            "test_id 只能包含英文字母、数字、下划线和连字符，且必须以字母或数字开头",
        )
    params = {"label": test_id, "total": req.total, "trials": 1, "personas": "",
              "concurrency": 0, "no_judge": req.no_judge, "seed": req.seed,
              "weights": req.weights if not req.dimensions else {}, "dimensions": req.dimensions}
    from ..cli import _load_models_cfg
    try:
        prepare_run(ROOT, td, test_id, _load_models_cfg(None), params)
    except FileExistsError:
        raise HTTPException(409, "运行 ID 已存在，请换一个 ID；已有结果不会被覆盖")
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    job_id = f"test_{test_id}"
    jobs.create(job_id, task_id=task_id, test_id=test_id, job_type="test")

    def _run_batch():
        try:
            cmd = [sys.executable, "-m", "claw_eval.cli", "batch",
                   "--task", task_id, "--total", str(req.total),
                   "--label", test_id, "--seed", str(req.seed), "--prepared", "--no-dashboard-out"]
            if req.no_judge:
                cmd.append("--no-judge")
            if req.dimensions:
                # 维度模式 → persona_factory(优先)
                cmd += ["--dimensions",
                          json.dumps(req.dimensions, ensure_ascii=False)]
            elif req.weights:
                cmd += ["--weights", json.dumps(req.weights, ensure_ascii=False)]
            env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
            proc = jobs.run_process(job_id, cmd, env=env, cwd=str(ROOT))
            from ..db import update_run
            run = get_run(test_id)
            if proc.returncode != 0 and (not run or run["status"] not in {"partial", "failed"}):
                update_run(test_id, status="failed", note=proc.stdout[-1000:])
            # Keep reports available even when only some cases completed.
            from ..report.builder import build_dashboard_from_dir
            run_traces = ROOT / "traces" / test_id
            run_report = REPORTS_DIR / test_id
            if (run_traces / "cases.json").exists() or any(run_traces.glob("*.result.json")):
                build_dashboard_from_dir(run_traces, run_report)
            if req.auto_recommend and proc.returncode == 0:
                rp = jobs.run_process(job_id,
                    [sys.executable, "-m", "claw_eval.cli", "recommend",
                     "--task", task_id, "--run-id", test_id],
                    env=env, cwd=str(ROOT), timeout=1800)
                if rp.returncode != 0:
                    raise RuntimeError(f"建议生成失败: {rp.stdout[-1500:]}")
            run = get_run(test_id)
            jobs.update(job_id, status=run["status"] if run else "failed")
        except Exception as exc:
            from ..db import update_run
            job = jobs.get(job_id)
            status = "canceled" if job and job["status"] in {"canceled", "canceling"} else "failed"
            jobs.update(job_id, status=status, log=str(exc))
            run = get_run(test_id)
            if run:
                if status == "failed" and run["status"] in {"done", "partial"}:
                    status = "partial"
                    jobs.update(job_id, status=status)
                from ..report.aggregate import load_results_dir
                completed = [r for r in load_results_dir(ROOT / "traces" / test_id) if r.status == "complete"]
                update_run(test_id, status=status, note=str(exc)[:1000], n_results=len(completed),
                           pass_rate=sum(r.passed is True for r in completed) / len(completed) if completed else None,
                           task_score_avg=sum(r.task_score for r in completed) / len(completed) if completed else None)

    background.add_task(_run_batch)
    return JobStatus(job_id=job_id, status="running",
                      message=f"测试 {test_id} 已启动")


@router.get("/jobs/test/{job_id}", response_model=JobStatus)
def get_test_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404)
    return JobStatus(job_id=job_id, status=job["status"], message=str(job.get("log", ""))[-3000:])


@router.post("/tasks/{task_id}/preview-personas", response_model=PreviewResult)
def preview_personas(task_id: str, req: PreviewRequest):
    """预览:按 dimensions 比例采样 N 次,看实际分布 + 前几个样本。"""
    from ..persona_factory import (
        generate_personas, preview_distribution,
    )
    try:
        validate_id(task_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
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
        raise HTTPException(422, str(exc))


@router.get("/tests/{test_id}/results")
def get_test_results(test_id: str):
    """读取测试的全部 case 结果,用于展示分布和明细。"""
    _require_run(test_id)
    traces_dir = ROOT / "traces" / test_id
    if not traces_dir.exists():
        return {"results": [], "heatmap": [], "scripts": [], "attitudes": []}

    from ..report.aggregate import load_results_dir
    try:
        results = [r.model_dump() for r in load_results_dir(traces_dir)]
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc))

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
        if r["status"] == "complete":
            cells.setdefault((sid, att), []).append(r["task_score"])

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
    _require_run(test_id)
    traces_dir = ROOT / "traces" / test_id
    if not traces_dir.exists():
        raise HTTPException(404, f"traces/{test_id} 不存在")

    from ..report.aggregate import load_results_dir
    n_results = len(load_results_dir(traces_dir))
    if n_results == 0:
        raise HTTPException(422, "该测试没有评分结果（.result.json），无法生成报告")

    run_report_dir = REPORTS_DIR / test_id
    job_key = f"report_{test_id}_{uuid4().hex[:8]}"
    jobs.create(job_key, task_id=get_run(test_id)["task_id"], test_id=test_id, job_type="report")

    def _build():
        try:
            from ..report.builder import build_dashboard_from_dir
            run_report_dir.mkdir(parents=True, exist_ok=True)
            build_dashboard_from_dir(traces_dir, run_report_dir)
            jobs.update(job_key, status="done")
        except Exception as exc:
            jobs.update(job_key, status="failed", log=str(exc))

    background.add_task(_build)
    return {"status": "generating", "job_id": job_key}


@router.get("/tests/{test_id}/report-status")
def get_test_report_status(test_id: str):
    """检查单次测试的报告是否存在。"""
    run = _require_run(test_id)
    page = REPORTS_DIR / test_id / f"task_{run['task_id']}.html"
    from ..report.builder import report_is_current
    current = report_is_current(page.parent, run['task_id'])
    return {"exists": current,
            "url": f"/reports/{test_id}/{page.name}?v={page.stat().st_mtime_ns}" if current else None}


def _require_run(test_id: str) -> dict:
    try:
        validate_id(test_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    run = get_run(test_id)
    if not run:
        raise HTTPException(404, "运行不存在")
    return run


@router.get("/tests/{test_id}/flow")
def get_test_flow(test_id: str):
    run = _require_run(test_id)
    if not (ROOT / "traces" / test_id / "manifest.json").exists():
        return {"nodes": [], "edges": [], "message": "历史运行没有流程快照"}
    try:
        load_manifest(ROOT, test_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc))
    import yaml
    path = ROOT / "traces" / test_id / "inputs" / "tasks" / run["task_id"] / "flow.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {"nodes": (data or {}).get("nodes", []), "edges": (data or {}).get("edges", [])}


@router.get("/tests/{test_id}/manifest")
def get_test_manifest(test_id: str):
    _require_run(test_id)
    try:
        return load_manifest(ROOT, test_id)
    except FileNotFoundError:
        raise HTTPException(404, "该历史运行没有输入快照")
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@router.get("/tests/{test_id}/recommendations")
def get_test_recommendations(test_id: str):
    run = _require_run(test_id)
    path = REPORTS_DIR / test_id / f"recommendations_{run['task_id']}.json"
    if not path.exists():
        return {"recommendations": [], "generated_at": None, "run_id": test_id, "status": "not_generated"}
    from ..report.recommend import recommendation_status
    data = json.loads(path.read_text(encoding="utf-8"))
    return {**data, **recommendation_status(data.get("recommendations", []))}


@router.get('/tests/{test_id}/coverage')
def get_coverage(test_id: str):
    run = _require_run(test_id)
    from ..report.aggregate import load_results_dir
    try:
        manifest = load_manifest(ROOT, test_id)
    except (OSError, ValueError) as exc:
        raise HTTPException(409, str(exc))
    directory = ROOT / 'traces' / test_id
    cases = json.loads((directory / 'cases.json').read_text()) if manifest.get('cases_hash') else []
    nodes = get_test_flow(test_id)['nodes']
    results = {r.case_id: r for r in load_results_dir(directory)}
    rows = []
    for node in nodes:
        planned, evidence, triggered = 0, [], 0
        for case in cases:
            stem = f"{run['task_id']}_{case['name']}_t{case['trial_idx'] + 1}"
            planned += node['id'] in case['persona'].get('covers_flow_nodes', [])
            result = results.get(stem)
            if result and any(r.rubric_id == node.get('rubric') and r.status == 'scored' for r in result.rubric_scores):
                triggered += 1
            trace = directory / f'{stem}.jsonl'
            if trace.exists():
                for line in trace.read_text(encoding='utf-8').splitlines():
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get('event') == 'turn' and (event.get('flow_node_id') == node['id'] or
                                                       (event.get('role') == 'user' and event.get('state') == node['id'])):
                        evidence.append({'case_id': stem, 'turn': event.get('turn')})
        rows.append({'node_id': node['id'], 'label': node['label'], 'planned_cases': planned,
                     'observed_cases': len({e['case_id'] for e in evidence}),
                     'rubric_scored_cases': triggered, 'evidence': evidence})
    return {'nodes': rows, 'total_cases': len(cases),
            'path_recording': 'available' if any(r['evidence'] for r in rows) else 'unavailable',
            'note': '计划覆盖来自剧本声明；实测访问只计算 trace 中明确记录的节点。评分项被触发不代表节点被访问。场景模式未记录节点时，实际路径未知。'}
