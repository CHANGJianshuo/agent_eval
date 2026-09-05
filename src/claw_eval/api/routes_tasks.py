"""任务相关 endpoints。"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from tempfile import NamedTemporaryFile

from . import jobs

import yaml
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from ..db import list_runs
from ..models.persona import PersonaScript
from ..models.rubric import Rubric, load_rubrics, save_rubrics
from ..task_gen.versioning import list_versions, get_version_files
from ..task_gen.config_store import commit_files, revision, task_files, EditConflict
from ..user_simulator.extractor import save_script


def _root() -> Path:
    cur = Path(__file__).resolve()
    for p in [cur, *cur.parents]:
        if (p / "pyproject.toml").exists():
            return p
    return Path.cwd()


ROOT = _root()
TASKS_DIR = ROOT / "tasks"
REPORTS_DIR = ROOT / "reports"
MODELS_FILE = ROOT / "configs" / "models.yaml"


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
    task_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    description: str = ""
    prompt: str


class JobStatus(BaseModel):
    job_id: str
    status: str
    message: str = ""
    step: int = 0
    total_steps: int = 4
    step_label: str = ""
    task_id: str = ""
    job_type: str = "generate"


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


def _rubrics_equivalent(left: Path, right: Path) -> bool:
    """Compare rule semantics while ignoring review-only metadata."""
    try:
        def _comparable(path: Path) -> list[dict]:
            rows = []
            for rubric in load_rubrics(path):
                row = rubric.model_dump(exclude_none=True)
                row.pop("reviewed", None)
                row.pop("confidence", None)
                rows.append(row)
            return rows

        return _comparable(left) == _comparable(right)
    except Exception:
        return False


def _milestones(task: str) -> dict[str, bool]:
    td = TASKS_DIR / task
    m1 = (td / "rubrics.yaml").exists()
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
    m3 = any(r.get("n_results", 0) > 0 for r in runs)
    from ..report.recommend import recommendations_complete
    m4 = any(recommendations_complete(REPORTS_DIR / r["run_id"] / f"recommendations_{task}.json") for r in runs)
    return {"m1": m1, "m2": m2, "m3": m3, "m4": m4}


def _resolve_api_key() -> str | None:
    """尝试从环境变量或本地存储获取 LLM API key。"""
    cfg = {}
    if MODELS_FILE.exists():
        try:
            cfg = yaml.safe_load(MODELS_FILE.read_text(encoding="utf-8")) or {}
        except Exception:
            pass
    env_var = cfg.get("provider", {}).get("api_key_env", "DEEPSEEK_API_KEY")
    key = os.environ.get(env_var)
    if key:
        return key
    keys_file = Path.home() / ".claw_eval" / "api_keys.yaml"
    if keys_file.exists():
        try:
            keys = yaml.safe_load(keys_file.read_text(encoding="utf-8")) or {}
            provider = env_var.removesuffix("_API_KEY").lower()
            if keys.get(provider):
                return str(keys[provider])
        except Exception:
            pass
    return None


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


_STEP_MARKERS = {
    "①": (1, "抽取业务变量"),
    "②": (2, "生成流程图"),
    "③": (3, "生成评分项"),
    "④": (4, "生成剧本与场景"),
}


@router.post("/tasks", response_model=JobStatus)
def create_task(req: NewTaskRequest, background: BackgroundTasks):
    """新建任务(后台异步跑 generate-task)。"""
    if not _resolve_api_key():
        raise HTTPException(400, "未配置 API Key，请先在设置页面添加")
    if req.task_id in _list_tasks():
        raise HTTPException(409, f"任务 {req.task_id} 已存在")
    if not req.prompt or len(req.prompt.strip()) < 50:
        raise HTTPException(400, "prompt 太短(<50 字)")

    job_id = f"gen_{req.task_id}_{uuid4().hex[:12]}"
    jobs.create(job_id, task_id=req.task_id, job_type="generate",
                total_steps=len(_STEP_MARKERS), step_label="初始化")

    def _run_generate():
        try:
            with NamedTemporaryFile(mode="w", suffix=".md", encoding="utf-8", delete=False) as f:
                f.write(req.prompt)
                tmp_p = Path(f.name)
            cmd = [sys.executable, "-u", "-m", "claw_eval.cli", "generate-task",
                   "--prompt", str(tmp_p), "--id", req.task_id]
            env = {**os.environ, "PYTHONPATH": str(ROOT / "src"),
                   "PYTHONUNBUFFERED": "1"}
            api_key = _resolve_api_key()
            if api_key:
                cfg = {}
                if MODELS_FILE.exists():
                    try:
                        cfg = yaml.safe_load(
                            MODELS_FILE.read_text(encoding="utf-8")) or {}
                    except Exception:
                        pass
                env_var = cfg.get("provider", {}).get(
                    "api_key_env", "DEEPSEEK_API_KEY")
                env[env_var] = api_key
            proc = jobs.run_process(job_id, cmd, env=env, cwd=str(ROOT),
                                    timeout=1800, markers=_STEP_MARKERS)
            if proc.returncode == 0:
                if req.description:
                    yp = TASKS_DIR / req.task_id / "task.yaml"
                    d = yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
                    d["description"] = req.description
                    yp.write_text(yaml.safe_dump(d, allow_unicode=True,
                                                  sort_keys=False), encoding="utf-8")
                jobs.update(job_id, status="done", step=len(_STEP_MARKERS), step_label="完成")
            else:
                jobs.update(job_id, status="failed", step_label="失败")
        except Exception as exc:
            jobs.update(job_id, status="failed", step_label="失败", log=str(exc))
        finally:
            if "tmp_p" in locals():
                tmp_p.unlink(missing_ok=True)

    background.add_task(_run_generate)
    return JobStatus(job_id=job_id, status="running",
                      message=f"生成任务 {req.task_id} 已启动")


def _job_response(job: dict) -> JobStatus:
    return JobStatus(**{**job, "message": str(job.get("log", ""))[-3000:]})


@router.get("/jobs", response_model=list[JobStatus])
def list_jobs():
    return [_job_response(job) for job in jobs.list_all()]


@router.get("/jobs/{job_id}", response_model=JobStatus)
def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return _job_response(job)


@router.post("/jobs/{job_id}/cancel", response_model=JobStatus)
def cancel_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    if job["job_type"] not in {"test", "generate"}:
        raise HTTPException(409, "该任务不支持取消")
    if job["status"] == "running":
        jobs.update(job_id, status="canceling", log="正在停止后台任务")
    return _job_response(jobs.get(job_id))


class ExtractMetaReq(BaseModel):
    prompt: str


@router.post("/extract-task-meta")
def extract_task_meta(req: ExtractMetaReq):
    """用 flash 模型从 SUT system prompt 自动提取 task_id + description。"""
    if len(req.prompt.strip()) < 20:
        raise HTTPException(400, "prompt 太短")

    # 允许调用方在 Prompt 中显式提供稳定标识。除了让重复演示更可控，
    # 也避免仅因一次元数据模型输出格式异常而阻断整个任务创建流程。
    explicit_id = re.search(
        r"(?im)^\s*(?:[#>*-]+\s*)?(?:demo\s+)?task[ _-]?id\s*[:：]?\s*`?"
        r"([a-z][a-z0-9_]*)`?\s*$",
        req.prompt,
    )
    if explicit_id:
        explicit_desc = re.search(
            r"(?im)^\s*(?:[#>*-]+\s*)?description\s*[:：]\s*(.{1,40})$",
            req.prompt,
        )
        description = explicit_desc.group(1).strip()[:20] if explicit_desc else ""
        return {
            "task_id": explicit_id.group(1).lower()[:64].rstrip("_"),
            "description": description,
        }

    api_key = _resolve_api_key()
    if not api_key:
        raise HTTPException(400, "未配置 API Key，请先在设置页面添加")

    cfg = {}
    if MODELS_FILE.exists():
        try:
            cfg = yaml.safe_load(MODELS_FILE.read_text(encoding="utf-8")) or {}
        except Exception:
            pass

    api_base = cfg.get("provider", {}).get("base_url")
    model = cfg.get("sut", {}).get("model", "deepseek-v4-flash")

    extract_prompt = (
        "根据以下 SUT System Prompt（外呼对话模型的指令），提取：\n"
        "1. task_id：英文小写 + 下划线，简短概括任务（如 meituan_rider、live_upgrade）\n"
        "2. description：一句中文简介（20字以内）\n\n"
        "只返回 JSON，格式：{\"task_id\": \"...\", \"description\": \"...\"}\n"
        "不要返回其他内容。\n\n"
        "--- SUT System Prompt ---\n"
        f"{req.prompt[:3000]}"
    )

    from ..runner import llm_client
    try:
        raw = llm_client.chat(
            model=model,
            messages=[{"role": "user", "content": extract_prompt}],
            temperature=0.0,
            max_tokens=200,
            api_base=api_base,
            api_key=api_key,
        )
        cleaned = re.sub(r"```json\s*|```", "", raw).strip()
        json_match = re.search(r"\{[\s\S]*\}", cleaned)
        result = json.loads(json_match.group(0) if json_match else cleaned)
        task_id = str(result.get("task_id", "new_task")).strip().lower()
        task_id = re.sub(r"[^a-z0-9_]+", "_", task_id)
        task_id = re.sub(r"_+", "_", task_id).strip("_") or "new_task"
        if not task_id[0].isalpha():
            task_id = f"task_{task_id}"
        task_id = task_id[:64].rstrip("_")
        description = str(result.get("description", ""))
        return {"task_id": task_id, "description": description}
    except Exception as exc:
        raise HTTPException(500, f"LLM 提取失败: {exc}")


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
        "revision": revision(yp.parent),
    }


class UpdatePromptReq(BaseModel):
    prompt: str
    description: str | None = None
    variables: dict | None = None
    expected_revision: str | None = None
    applied_recs: list[str] = Field(default_factory=list)


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
    if req.variables is not None:
        d["variables"] = req.variables
    return _commit(yp.parent, {"task.yaml": yaml.safe_dump(d, allow_unicode=True, sort_keys=False)},
                   expected_revision=req.expected_revision, note="保存 Prompt 与业务变量",
                   applied_recs=req.applied_recs)



@router.get("/tasks/{task_id}/rubrics")
def get_task_rubrics(task_id: str):
    """读待审草稿；没有待审修改时读已生效 rubrics.yaml。"""
    td = TASKS_DIR / task_id
    final = td / "rubrics.yaml"
    draft = td / "rubrics.draft.yaml"
    is_draft = draft.exists() and (
        not final.exists() or not _rubrics_equivalent(draft, final)
    )
    rb = draft if is_draft else final
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


class UpdateRubricsReq(BaseModel):
    rubrics: list[dict]
    is_draft: bool = False


@router.put("/tasks/{task_id}/rubrics")
def update_rubrics(task_id: str, req: UpdateRubricsReq):
    """保存 rubrics（写入当前生效的文件：rubrics.yaml 或 rubrics.draft.yaml）。"""
    td = TASKS_DIR / task_id
    if not td.exists():
        raise HTTPException(404)
    try:
        rubrics = [Rubric.model_validate(row) for row in req.rubrics]
    except Exception as exc:
        raise HTTPException(422, f"Rubric 校验失败: {exc}") from exc
    if not rubrics:
        raise HTTPException(422, "Rubric 列表不能为空")

    final = td / "rubrics.yaml"
    # Editing a generated-only task must not create an approved file. For an
    # established task, the caller explicitly tells us whether it is editing
    # the pending draft or the active rule set.
    rb = td / "rubrics.draft.yaml" if req.is_draft or not final.exists() else final
    saved = _commit(td, {rb.name: yaml.safe_dump({"rubrics": [r.model_dump(exclude_none=True) for r in rubrics]}, allow_unicode=True)}, note="编辑评分项")
    return {**saved, "count": len(rubrics), "file": rb.name}


@router.get("/tasks/{task_id}/flow")
def get_task_flow(task_id: str):
    """读 flow.yaml（节点 + 边）。"""
    td = TASKS_DIR / task_id
    fp = td / "flow.yaml"
    if not fp.exists():
        return {"nodes": [], "edges": []}
    data = yaml.safe_load(fp.read_text(encoding="utf-8")) or {}
    return {
        "nodes": data.get("nodes", []),
        "edges": data.get("edges", []),
    }


@router.get("/tasks/{task_id}/versions")
def get_task_versions(task_id: str):
    """读版本历史。"""
    td = TASKS_DIR / task_id
    versions = list_versions(td)
    from dataclasses import asdict
    return {"versions": [asdict(v) for v in versions]}


@router.get("/tasks/{task_id}/review-status")
def get_review_status(task_id: str):
    """审核状态:哪些还是草稿,哪些已转正。"""
    td = TASKS_DIR / task_id
    if not td.exists():
        raise HTTPException(404, f"task {task_id} 不存在")
    has_rubrics = (td / "rubrics.yaml").exists()
    has_draft_rubrics = (td / "rubrics.draft.yaml").exists()

    rubrics_pending = False
    if has_draft_rubrics:
        if not has_rubrics:
            rubrics_pending = True
        else:
            rubrics_pending = not _rubrics_equivalent(
                td / "rubrics.draft.yaml", td / "rubrics.yaml"
            )

    personas_dir = td / "personas"
    draft_dir = td / "personas_draft"
    approved = sorted(p.stem for p in personas_dir.glob("*.yaml")) if personas_dir.exists() else []
    drafts = sorted(p.stem for p in draft_dir.glob("*.yaml")) if draft_dir.exists() else []

    def _same_yaml(left: Path, right: Path) -> bool:
        try:
            left_data = yaml.safe_load(left.read_text(encoding="utf-8")) or {}
            right_data = yaml.safe_load(right.read_text(encoding="utf-8")) or {}
            return PersonaScript.model_validate(left_data).model_dump() == \
                PersonaScript.model_validate(right_data).model_dump()
        except Exception:
            return False

    pending = [
        pid for pid in drafts
        if not (personas_dir / f"{pid}.yaml").exists()
        or not _same_yaml(
            draft_dir / f"{pid}.yaml",
            personas_dir / f"{pid}.yaml",
        )
    ]
    return {
        "rubrics_approved": has_rubrics,
        "rubrics_draft": rubrics_pending,
        "personas_approved": approved,
        "personas_pending": pending,
    }


class ApproveReq(BaseModel):
    approve_rubrics: bool = False
    approve_personas: list[str] = Field(default_factory=list)


@router.post("/tasks/{task_id}/approve")
def approve_drafts(task_id: str, req: ApproveReq):
    """人审转正:rubrics.draft → rubrics.yaml,personas_draft/ → personas/。"""
    td = TASKS_DIR / task_id
    if not td.exists():
        raise HTTPException(404)

    results: list[str] = []
    staged_rubrics: list[Rubric] | None = None

    if req.approve_rubrics:
        draft = td / "rubrics.draft.yaml"
        if draft.exists():
            try:
                staged_rubrics = [
                    rubric.model_copy(update={
                        "reviewed": True,
                        "confidence": None,
                    })
                    for rubric in load_rubrics(draft)
                ]
            except Exception as exc:
                raise HTTPException(
                    422, f"Rubric 草稿校验失败，未转正: {exc}"
                ) from exc
            if not staged_rubrics:
                raise HTTPException(422, "Rubric 草稿为空，未转正")
        else:
            results.append("无 rubrics 草稿")

    personas_dir = td / "personas"
    draft_dir = td / "personas_draft"
    available = {
        path.stem: path for path in draft_dir.glob("*.yaml")
    } if draft_dir.exists() else {}
    unknown = sorted(set(req.approve_personas) - set(available))
    if unknown:
        raise HTTPException(422, f"不存在的 persona 草稿: {unknown}")

    staged_personas: dict[str, PersonaScript] = {}
    for pid in req.approve_personas:
        src = available[pid]
        try:
            data = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
            script = PersonaScript.model_validate(data)
        except Exception as exc:
            raise HTTPException(
                422, f"Persona 草稿 {pid} 校验失败，未转正: {exc}"
            ) from exc
        if script.id != pid:
            raise HTTPException(
                422, f"Persona 草稿文件名 {pid!r} 与 id {script.id!r} 不一致"
            )
        staged_personas[pid] = script

    changes = {}
    if staged_rubrics is not None:
        changes["rubrics.yaml"] = yaml.safe_dump({"rubrics": [r.model_dump(exclude_none=True) for r in staged_rubrics]}, allow_unicode=True)
        results.append(f"rubrics 已转正（{len(staged_rubrics)} 条）")
    for pid, script in staged_personas.items():
        changes[f"personas/{pid}.yaml"] = yaml.safe_dump(script.model_dump(exclude_none=True), allow_unicode=True)
        results.append(f"persona {pid} 已转正")
    saved = _commit(td, changes, note="审核草稿并转正") if changes else {"ok": True}
    return {**saved, "results": results}



# ==================== Agent Chat ====================

class AgentChatReq(BaseModel):
    messages: list[dict]


_AGENT_SYSTEM = """\
你是评测系统的配置修改助手。用户会用自然语言描述想修改的内容,你需要:

1. 理解用户的修改意图
2. 输出具体的修改操作(JSON 格式)
3. 用友好的中文回复确认改了什么

你可以修改的内容:
- rubrics: 修改 weight、check、删除、新增评分项
- scripts: 修改场景描述、探针、覆盖节点

当前任务的配置会作为上下文提供给你。

回复格式(严格 JSON):
```json
{
  "reply": "你的中文回复",
  "actions": [
    {"type": "update_rubric", "rubric_id": "xxx", "field": "weight", "value": 0.15},
    {"type": "delete_rubric", "rubric_id": "xxx"},
    {"type": "add_rubric", "rubric": {"id": "safety.xxx", "category": "safety", ...}},
    {"type": "update_script", "script_id": "xxx", "field": "scenario", "value": "..."},
    {"type": "add_probe", "script_id": "xxx", "probe": {"id": "...", "inject_at_turn": 3, "text": "..."}},
    {"type": "delete_probe", "script_id": "xxx", "probe_id": "xxx"}
  ]
}
```

如果用户只是问问题而不需要修改,actions 为空列表。
只输出 JSON,不要其他文字。
"""


@router.post("/tasks/{task_id}/agent-chat")
def agent_chat(task_id: str, req: AgentChatReq):
    """Agent 对话:理解用户意图并修改任务配置。"""
    td = TASKS_DIR / task_id
    if not td.exists():
        raise HTTPException(404)

    api_key = _resolve_api_key()
    if not api_key:
        return {"reply": "未配置 API Key，请先在设置页面添加。", "applied": False}

    # 读取当前配置作为上下文
    context_parts = []

    # rubrics
    final_rb = td / "rubrics.yaml"
    draft_rb = td / "rubrics.draft.yaml"
    rb = (
        draft_rb
        if draft_rb.exists() and (
            not final_rb.exists() or not _rubrics_equivalent(draft_rb, final_rb)
        )
        else final_rb
    )
    if rb.exists():
        try:
            rubrics = load_rubrics(rb)
            rubrics_data = [r.model_dump(exclude_none=True) for r in rubrics]
            context_parts.append(f"当前 Rubrics ({len(rubrics_data)} 条):\n"
                                 + yaml.safe_dump(rubrics_data, allow_unicode=True))
        except Exception:
            pass

    # scripts
    scripts_data = []
    for d in [td / "personas", td / "personas_draft"]:
        if d.exists():
            for f in sorted(d.glob("*.yaml")):
                try:
                    s = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
                    s["_file"] = str(f.relative_to(td))
                    scripts_data.append(s)
                except Exception:
                    pass
    if scripts_data:
        context_parts.append(f"当前剧本 ({len(scripts_data)} 个):\n"
                             + yaml.safe_dump(scripts_data, allow_unicode=True))

    # flow
    fp = td / "flow.yaml"
    if fp.exists():
        context_parts.append(f"Flow:\n{fp.read_text(encoding='utf-8')}")

    context_text = "\n---\n".join(context_parts) if context_parts else "(无配置)"

    # 构建 LLM 消息
    llm_messages = [{"role": "system", "content": _AGENT_SYSTEM}]
    llm_messages.append({"role": "user", "content": f"任务 {task_id} 的当前配置:\n\n{context_text}"})
    for m in req.messages:
        if m.get("role") in ("user", "assistant"):
            llm_messages.append({"role": m["role"], "content": m["content"]})

    # 调 LLM
    from ..runner import llm_client
    cfg = {}
    models_file = td.parent.parent / "configs" / "models.yaml"
    if models_file.exists():
        try:
            cfg = yaml.safe_load(models_file.read_text(encoding="utf-8")) or {}
        except Exception:
            pass

    provider = cfg.get("provider", {})
    base_url = provider.get("base_url", "")
    env_var = provider.get("api_key_env", "DEEPSEEK_API_KEY")
    llm_client.configure(api_base=base_url, api_key=api_key)

    judge_cfg = cfg.get("extract_rubric", {})
    model = judge_cfg.get("model", "deepseek-v4-pro")

    try:
        raw = llm_client.chat(model, llm_messages, temperature=0.0,
                              reasoning_effort="low", max_tokens=4000)
    except Exception as exc:
        return {"reply": f"LLM 调用失败: {exc}", "applied": False}

    # 解析 JSON
    m = re.search(r"```(?:json)?\s*\n(.+?)```", raw, re.DOTALL)
    json_str = m.group(1) if m else raw
    try:
        result = json.loads(json_str)
    except json.JSONDecodeError:
        return {"reply": raw, "applied": False}

    reply = result.get("reply", "")
    actions = result.get("actions", [])

    if not actions:
        return {"reply": reply, "applied": False}

    # 执行操作。AI 修改一律写草稿，只有 /approve 能转为正式配置。
    applied_count = 0
    rubrics_changed = False
    rubrics_source = draft_rb if draft_rb.exists() else final_rb
    rubrics_list: list[dict] = []
    if rubrics_source.exists():
        try:
            rubrics_list = [
                rubric.model_dump(exclude_none=True)
                for rubric in load_rubrics(rubrics_source)
            ]
        except Exception as exc:
            return {
                "reply": f"当前 Rubric 无法读取，未应用修改: {exc}",
                "applied": False,
            }

    script_sources: dict[str, Path] = {}
    # 已批准版本作为基线；已有草稿覆盖它，以便连续修改同一份草稿。
    for directory in [td / "personas", td / "personas_draft"]:
        if directory.exists():
            for path in directory.glob("*.yaml"):
                script_sources[path.stem] = path
    staged_scripts: dict[str, PersonaScript] = {}
    allowed_rubric_fields = {
        "dimension", "method", "check", "weight", "trigger",
        "is_safety", "params", "category",
    }
    allowed_script_fields = {
        "name", "scenario", "covers_flow_nodes", "max_rounds", "noise",
        "personality", "states", "initial_state", "transitions",
    }

    try:
        for act in actions:
            if not isinstance(act, dict):
                continue
            action_type = act.get("type", "")
            if action_type == "update_rubric":
                rid = str(act.get("rubric_id", ""))
                field_name = str(act.get("field", ""))
                if field_name not in allowed_rubric_fields:
                    continue
                for row in rubrics_list:
                    if row.get("id") == rid:
                        row[field_name] = act.get("value")
                        row["reviewed"] = False
                        applied_count += 1
                        rubrics_changed = True
                        break
            elif action_type == "delete_rubric":
                rid = str(act.get("rubric_id", ""))
                before = len(rubrics_list)
                rubrics_list = [row for row in rubrics_list if row.get("id") != rid]
                if len(rubrics_list) < before:
                    applied_count += 1
                    rubrics_changed = True
            elif action_type == "add_rubric":
                new_row = act.get("rubric")
                if isinstance(new_row, dict) and new_row.get("id"):
                    if any(row.get("id") == new_row["id"] for row in rubrics_list):
                        raise ValueError(f"Rubric id 已存在: {new_row['id']}")
                    rubrics_list.append({**new_row, "reviewed": False})
                    applied_count += 1
                    rubrics_changed = True
            elif action_type in ("update_script", "add_probe", "delete_probe"):
                sid = str(act.get("script_id", ""))
                source = script_sources.get(sid)
                if not source:
                    continue
                if sid in staged_scripts:
                    script_data = staged_scripts[sid].model_dump(exclude_none=True)
                else:
                    script_data = yaml.safe_load(
                        source.read_text(encoding="utf-8")
                    ) or {}

                if action_type == "update_script":
                    field_name = str(act.get("field", ""))
                    if field_name not in allowed_script_fields:
                        continue
                    script_data[field_name] = act.get("value")
                elif action_type == "add_probe":
                    probe = act.get("probe")
                    if not isinstance(probe, dict):
                        continue
                    script_data.setdefault("probes", []).append(probe)
                else:
                    probe_id = str(act.get("probe_id", ""))
                    old_probes = script_data.get("probes", [])
                    new_probes = [p for p in old_probes if p.get("id") != probe_id]
                    if len(new_probes) == len(old_probes):
                        continue
                    script_data["probes"] = new_probes

                script = PersonaScript.model_validate(script_data)
                if script.id != sid:
                    raise ValueError(
                        f"Persona 文件名 {sid!r} 与 id {script.id!r} 不一致"
                    )
                staged_scripts[sid] = script
                applied_count += 1

        validated_rubrics = (
            [Rubric.model_validate(row) for row in rubrics_list]
            if rubrics_changed else []
        )
        if rubrics_changed and not validated_rubrics:
            raise ValueError("不能删除全部 Rubric")
    except Exception as exc:
        return {
            "reply": f"修改未通过配置校验，未写入文件: {exc}",
            "applied": False,
        }

    if rubrics_changed:
        save_rubrics(validated_rubrics, draft_rb, include_meta=True)
    if staged_scripts:
        draft_dir = td / "personas_draft"
        draft_dir.mkdir(exist_ok=True)
        for sid, script in staged_scripts.items():
            save_script(script, draft_dir / f"{sid}.yaml")

    return {
        "reply": reply + (
            f"\n\n(已写入 {applied_count} 项草稿修改，请审核后转正)"
            if applied_count else ""
        ),
        "applied": applied_count > 0,
    }


def _commit(td: Path, changes: dict, **kwargs):
    try:
        return commit_files(td, changes, **kwargs)
    except EditConflict as exc:
        raise HTTPException(409, str(exc))
    except (ValueError, OSError, KeyError) as exc:
        raise HTTPException(422, str(exc))


def _existing_task(task_id: str) -> Path:
    from ..runs import validate_id
    try:
        validate_id(task_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    td = TASKS_DIR / task_id
    if not (td / "task.yaml").exists():
        raise HTTPException(404, "任务不存在")
    return td


class ConfigEditReq(BaseModel):
    files: dict[str, str | None]
    expected_revision: str
    note: str = "手工编辑配置"


@router.get("/tasks/{task_id}/configuration")
def get_configuration(task_id: str):
    td = _existing_task(task_id)
    return {"files": task_files(td), "revision": revision(td)}


@router.put("/tasks/{task_id}/configuration")
def update_configuration(task_id: str, req: ConfigEditReq):
    td = _existing_task(task_id)
    return _commit(td, req.files, expected_revision=req.expected_revision, note=req.note)


@router.get("/tasks/{task_id}/validate")
def validate_configuration(task_id: str):
    from dataclasses import asdict
    from ..validator import validate_task
    td = _existing_task(task_id)
    try:
        rep = validate_task(td, personalities_dir=td.parent.parent / "personalities",
                            noise_file=td.parent.parent / "configs/noise_profiles.yaml",
                            sampling_file=td / "sampling.yaml")
        return {"ok": rep.ok, "issues": [asdict(i) for i in rep.issues]}
    except Exception as exc:
        return {"ok": False, "issues": [{"level": "error", "code": "load", "message": str(exc)}]}


@router.get("/tasks/{task_id}/versions/{label}")
def version_detail(task_id: str, label: str):
    from ..task_gen.apply_recommendation import unified_diff
    td = _existing_task(task_id)
    try:
        files = get_version_files(td, label)
    except (ValueError, OSError) as exc:
        raise HTTPException(404, str(exc))
    current = task_files(td)
    diff = "\n".join(unified_diff(current.get(n, ""), files.get(n, ""), f"current/{n}", f"{label}/{n}")
                     for n in sorted(set(current) | set(files)))
    return {"files": files, "diff": diff, "revision": revision(td),
            "complete_snapshot": (td / ".versions" / f"{label}.snapshot.json").exists()}


class RestoreReq(BaseModel):
    expected_revision: str


@router.post("/tasks/{task_id}/versions/{label}/restore")
def restore_version(task_id: str, label: str, req: RestoreReq):
    td = _existing_task(task_id)
    detail = version_detail(task_id, label)
    files = detail["files"]
    changes = {n: files.get(n) for n in set(task_files(td)) | set(files)}
    return _commit(td, changes, expected_revision=req.expected_revision, note=f"恢复版本 {label}")


@router.get('/tasks/{task_id}/documents')
def task_documents(task_id: str):
    td = _existing_task(task_id)
    return {'documents': {name: yaml.safe_load(text) for name, text in task_files(td).items()}}
