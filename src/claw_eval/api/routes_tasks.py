"""任务相关 endpoints。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from ..db import list_runs
from ..models.rubric import load_rubrics
from ..task_gen.versioning import list_versions


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


# ============================ models ============================

class TaskListItem(BaseModel):
    task_id: str
    description: str = ""
    n_rubrics: int = 0
    n_personas: int = 0
    n_adv_personas: int = 0
    n_versions: int = 0
    n_tests: int = 0
    last_pass_rate: float | None = None
    milestones: dict[str, bool] = {}     # m1/m2/m3/m4


class TaskDetail(TaskListItem):
    prompt: str = ""
    variables: dict = {}
    has_flow: bool = False


class NewTaskRequest(BaseModel):
    task_id: str
    description: str = ""
    prompt: str


class JobStatus(BaseModel):
    job_id: str
    status: str
    message: str = ""


# ============================ helpers ============================

def _list_tasks() -> list[str]:
    if not TASKS_DIR.exists():
        return []
    return sorted(d.name for d in TASKS_DIR.iterdir() if d.is_dir())


def _list_personas(task: str) -> list[str]:
    pd = TASKS_DIR / task / "personas"
    if not pd.exists():
        return []
    return sorted(p.stem for p in pd.glob("*.yaml"))


def _task_brief(task: str) -> str:
    yp = TASKS_DIR / task / "task.yaml"
    if not yp.exists():
        return ""
    try:
        d = yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
        if d.get("description"):
            return str(d["description"])
        prompt = str(d.get("prompt", ""))
        for line in prompt.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line[:120]
    except Exception:
        pass
    return ""


def _milestones(task: str) -> dict[str, bool]:
    td = TASKS_DIR / task
    m1 = (td / "rubrics.yaml").exists() and (td / "grader.py").exists()
    has_p = len(_list_personas(task)) > 0
    has_weights = False
    if (td / "sampling.yaml").exists():
        try:
            sd = yaml.safe_load((td / "sampling.yaml").read_text(encoding="utf-8")) or {}
            has_weights = bool(sd.get("weights"))
        except Exception:
            pass
    m2 = has_p and has_weights
    runs = list_runs(task_id=task)
    m3 = len(runs) >= 1
    has_rec = (REPORTS_DIR / f"recommendations_{task}.json").exists()
    m4 = has_rec or len(runs) >= 2
    return {"m1": m1, "m2": m2, "m3": m3, "m4": m4}


def _task_summary(task: str) -> TaskListItem:
    td = TASKS_DIR / task
    n_rubrics = 0
    try:
        if (td / "rubrics.yaml").exists():
            n_rubrics = len(load_rubrics(td / "rubrics.yaml"))
        elif (td / "rubrics.draft.yaml").exists():
            n_rubrics = len(load_rubrics(td / "rubrics.draft.yaml"))
    except Exception:
        pass
    p_list = _list_personas(task)
    runs = list_runs(task_id=task, limit=1)
    last_pass = runs[0].get("pass_rate") if runs else None
    return TaskListItem(
        task_id=task,
        description=_task_brief(task),
        n_rubrics=n_rubrics,
        n_personas=len([p for p in p_list if not p.startswith("adv_")]),
        n_adv_personas=len([p for p in p_list if p.startswith("adv_")]),
        n_versions=len(list_versions(td)),
        n_tests=len(list_runs(task_id=task)),
        last_pass_rate=last_pass,
        milestones=_milestones(task),
    )


# ============================ endpoints ============================

@router.get("/tasks", response_model=list[TaskListItem])
def list_tasks():
    """所有任务概览。"""
    return [_task_summary(t) for t in _list_tasks()]


@router.get("/tasks/{task_id}", response_model=TaskDetail)
def get_task(task_id: str):
    """单任务详情。"""
    if task_id not in _list_tasks():
        raise HTTPException(404, f"task {task_id} 不存在")
    summary = _task_summary(task_id)
    yp = TASKS_DIR / task_id / "task.yaml"
    prompt = ""
    variables = {}
    if yp.exists():
        try:
            d = yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
            prompt = str(d.get("prompt", ""))
            variables = d.get("variables", {})
        except Exception:
            pass
    has_flow = (TASKS_DIR / task_id / "flow.yaml").exists()
    return TaskDetail(
        **summary.model_dump(),
        prompt=prompt, variables=variables, has_flow=has_flow,
    )


_GEN_JOBS: dict[str, dict] = {}  # job_id → status dict


@router.post("/tasks", response_model=JobStatus)
def create_task(req: NewTaskRequest, background: BackgroundTasks):
    """新建任务(后台异步跑 generate-task)。"""
    if req.task_id in _list_tasks():
        raise HTTPException(409, f"任务 {req.task_id} 已存在")
    if not req.prompt or len(req.prompt.strip()) < 50:
        raise HTTPException(400, "prompt 太短(<50 字)")

    job_id = f"gen_{req.task_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    _GEN_JOBS[job_id] = {
        "status": "running",
        "task_id": req.task_id,
        "log": [],
    }

    def _run_generate():
        try:
            tmp_p = Path(f"/tmp/_gen_{req.task_id}.md")
            tmp_p.write_text(req.prompt, encoding="utf-8")
            cmd = [sys.executable, "-m", "claw_eval.cli", "generate-task",
                   "--prompt", str(tmp_p), "--id", req.task_id]
            env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                env=env, cwd=str(ROOT))
            _GEN_JOBS[job_id]["log"] = proc.stdout[-3000:]
            if proc.returncode == 0:
                if req.description:
                    yp = TASKS_DIR / req.task_id / "task.yaml"
                    d = yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
                    d["description"] = req.description
                    yp.write_text(yaml.safe_dump(d, allow_unicode=True,
                                                  sort_keys=False), encoding="utf-8")
                _GEN_JOBS[job_id]["status"] = "done"
            else:
                _GEN_JOBS[job_id]["status"] = "failed"
        except Exception as exc:
            _GEN_JOBS[job_id]["status"] = "failed"
            _GEN_JOBS[job_id]["log"] = str(exc)

    background.add_task(_run_generate)
    return JobStatus(job_id=job_id, status="running",
                      message=f"生成任务 {req.task_id} 已启动(后台,~3-5 min)")


@router.get("/jobs/{job_id}", response_model=JobStatus)
def get_job(job_id: str):
    """查异步 job 状态。"""
    if job_id not in _GEN_JOBS:
        raise HTTPException(404, f"job {job_id} 不存在")
    j = _GEN_JOBS[job_id]
    return JobStatus(job_id=job_id, status=j["status"],
                      message=str(j.get("log", ""))[-500:])


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str):
    """删除任务(rm -rf tasks/<id>/)。"""
    if task_id not in _list_tasks():
        raise HTTPException(404, f"task {task_id} 不存在")
    shutil.rmtree(TASKS_DIR / task_id)
    return {"deleted": task_id}


@router.get("/tasks/{task_id}/prompt")
def get_task_prompt(task_id: str):
    """读 task.yaml 的 prompt(供编辑)。"""
    yp = TASKS_DIR / task_id / "task.yaml"
    if not yp.exists():
        raise HTTPException(404)
    d = yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
    return {
        "prompt": d.get("prompt", ""),
        "variables": d.get("variables", {}),
        "description": d.get("description", ""),
    }


class UpdatePromptReq(BaseModel):
    prompt: str
    description: str | None = None


@router.put("/tasks/{task_id}/prompt")
def update_task_prompt(task_id: str, req: UpdatePromptReq):
    """改 task.yaml 的 prompt。"""
    yp = TASKS_DIR / task_id / "task.yaml"
    if not yp.exists():
        raise HTTPException(404)
    d = yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
    d["prompt"] = req.prompt
    if req.description is not None:
        d["description"] = req.description
    yp.write_text(yaml.safe_dump(d, allow_unicode=True, sort_keys=False),
                   encoding="utf-8")
    return {"ok": True}


@router.get("/tasks/{task_id}/rubrics")
def get_task_rubrics(task_id: str):
    """读 rubrics.yaml(若不存在,试 rubrics.draft.yaml)。"""
    td = TASKS_DIR / task_id
    rb = td / "rubrics.yaml"
    is_draft = False
    if not rb.exists():
        rb = td / "rubrics.draft.yaml"
        is_draft = True
    if not rb.exists():
        return {"rubrics": [], "is_draft": False}
    try:
        rubrics = load_rubrics(rb)
        return {
            "rubrics": [r.model_dump(exclude_none=True) for r in rubrics],
            "is_draft": is_draft,
        }
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.get("/tasks/{task_id}/versions")
def get_task_versions(task_id: str):
    """读版本历史。"""
    td = TASKS_DIR / task_id
    versions = list_versions(td)
    from dataclasses import asdict
    return {"versions": [asdict(v) for v in versions]}
