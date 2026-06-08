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
    task_id: str
    description: str = ""
    prompt: str


class JobStatus(BaseModel):
    job_id: str
    status: str
    message: str = ""
    step: int = 0
    total_steps: int = 5
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
            for prov in ["deepseek", "xiaomi_mimo", "openai", "anthropic"]:
                if keys.get(prov):
                    return str(keys[prov])
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


_GEN_JOBS: dict[str, dict] = {}  # job_id → status dict


_STEP_MARKERS = {
    "①": (1, "抽取业务变量"),
    "②": (2, "生成流程图"),
    "③": (3, "生成评分项"),
    "④": (4, "生成剧本与场景"),
    "⑤": (5, "生成评分器"),
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

    job_id = f"gen_{req.task_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    _GEN_JOBS[job_id] = {
        "status": "running",
        "task_id": req.task_id,
        "step": 0,
        "total_steps": 5,
        "step_label": "初始化",
        "log": "",
    }

    def _run_generate():
        try:
            tmp_p = Path(f"/tmp/_gen_{req.task_id}.md")
            tmp_p.write_text(req.prompt, encoding="utf-8")
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
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=env, cwd=str(ROOT))
            log_lines: list[str] = []
            for line in iter(proc.stdout.readline, ""):  # type: ignore[union-attr]
                log_lines.append(line.rstrip())
                for marker, (step_num, label) in _STEP_MARKERS.items():
                    if marker in line:
                        _GEN_JOBS[job_id]["step"] = step_num
                        _GEN_JOBS[job_id]["step_label"] = label
                        break
            proc.wait()
            _GEN_JOBS[job_id]["log"] = "\n".join(log_lines[-100:])
            if proc.returncode == 0:
                if req.description:
                    yp = TASKS_DIR / req.task_id / "task.yaml"
                    d = yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
                    d["description"] = req.description
                    yp.write_text(yaml.safe_dump(d, allow_unicode=True,
                                                  sort_keys=False), encoding="utf-8")
                _GEN_JOBS[job_id]["status"] = "done"
                _GEN_JOBS[job_id]["step"] = 5
                _GEN_JOBS[job_id]["step_label"] = "完成"
            else:
                _GEN_JOBS[job_id]["status"] = "failed"
                _GEN_JOBS[job_id]["step_label"] = "失败"
        except Exception as exc:
            _GEN_JOBS[job_id]["status"] = "failed"
            _GEN_JOBS[job_id]["step_label"] = "失败"
            _GEN_JOBS[job_id]["log"] = str(exc)

    background.add_task(_run_generate)
    return JobStatus(job_id=job_id, status="running",
                      message=f"生成任务 {req.task_id} 已启动")


@router.get("/jobs", response_model=list[JobStatus])
def list_jobs():
    """返回所有后台任务（含已完成），供前端刷新后恢复状态。"""
    return [
        JobStatus(
            job_id=jid,
            status=j["status"],
            message=str(j.get("log", ""))[-500:],
            step=j.get("step", 0),
            total_steps=j.get("total_steps", 5),
            step_label=j.get("step_label", ""),
            task_id=j.get("task_id", ""),
            job_type=j.get("job_type", "generate"),
        )
        for jid, j in _GEN_JOBS.items()
    ]


@router.get("/jobs/{job_id}", response_model=JobStatus)
def get_job(job_id: str):
    """查异步 job 状态。"""
    if job_id not in _GEN_JOBS:
        raise HTTPException(404, f"job {job_id} 不存在")
    j = _GEN_JOBS[job_id]
    return JobStatus(
        job_id=job_id,
        status=j["status"],
        message=str(j.get("log", ""))[-500:],
        step=j.get("step", 0),
        total_steps=j.get("total_steps", 5),
        step_label=j.get("step_label", ""),
        task_id=j.get("task_id", ""),
        job_type=j.get("job_type", "generate"),
    )


class ExtractMetaReq(BaseModel):
    prompt: str


@router.post("/extract-task-meta")
def extract_task_meta(req: ExtractMetaReq):
    """用 flash 模型从 SUT system prompt 自动提取 task_id + description。"""
    if len(req.prompt.strip()) < 20:
        raise HTTPException(400, "prompt 太短")

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
        result = json.loads(cleaned)
        task_id = str(result.get("task_id", "new_task")).strip()
        task_id = re.sub(r"[\/\\<>:\"|?*\s]+", "_", task_id)
        task_id = re.sub(r"_+", "_", task_id).strip("_") or "new_task"
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


class UpdateRubricsReq(BaseModel):
    rubrics: list[dict]


@router.put("/tasks/{task_id}/rubrics")
def update_rubrics(task_id: str, req: UpdateRubricsReq):
    """保存 rubrics（写入当前生效的文件：rubrics.yaml 或 rubrics.draft.yaml）。"""
    td = TASKS_DIR / task_id
    if not td.exists():
        raise HTTPException(404)
    rb = td / "rubrics.yaml"
    if not rb.exists():
        rb = td / "rubrics.draft.yaml"
    rb.write_text(
        yaml.safe_dump(req.rubrics, allow_unicode=True, sort_keys=False,
                       default_flow_style=False),
        encoding="utf-8")
    return {"ok": True, "count": len(req.rubrics)}


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
    has_rubrics = (td / "rubrics.yaml").exists()
    has_draft_rubrics = (td / "rubrics.draft.yaml").exists()
    personas_dir = td / "personas"
    draft_dir = td / "personas_draft"
    approved = sorted(p.stem for p in personas_dir.glob("*.yaml")) if personas_dir.exists() else []
    drafts = sorted(p.stem for p in draft_dir.glob("*.yaml")) if draft_dir.exists() else []
    pending = [d for d in drafts if d not in approved]
    return {
        "rubrics_approved": has_rubrics,
        "rubrics_draft": has_draft_rubrics and not has_rubrics,
        "personas_approved": approved,
        "personas_pending": pending,
    }


class ApproveReq(BaseModel):
    approve_rubrics: bool = False
    approve_personas: list[str] = []


@router.post("/tasks/{task_id}/approve")
def approve_drafts(task_id: str, req: ApproveReq):
    """人审转正:rubrics.draft → rubrics.yaml,personas_draft/ → personas/。"""
    td = TASKS_DIR / task_id
    if not td.exists():
        raise HTTPException(404)

    results: list[str] = []

    if req.approve_rubrics:
        draft = td / "rubrics.draft.yaml"
        final = td / "rubrics.yaml"
        if draft.exists():
            shutil.copy2(draft, final)
            results.append(f"rubrics 已转正（{sum(1 for _ in open(final))} 行）")
        else:
            results.append("无 rubrics 草稿")

    personas_dir = td / "personas"
    draft_dir = td / "personas_draft"
    personas_dir.mkdir(exist_ok=True)
    for pid in req.approve_personas:
        src = draft_dir / f"{pid}.yaml"
        dst = personas_dir / f"{pid}.yaml"
        if src.exists():
            shutil.copy2(src, dst)
            results.append(f"persona {pid} 已转正")

    return {"ok": True, "results": results}


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
- flow: 修改节点、边

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
    rb = td / "rubrics.yaml"
    if not rb.exists():
        rb = td / "rubrics.draft.yaml"
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

    # 执行操作
    applied_count = 0

    # 加载 rubrics 用于修改
    rubrics_file = td / "rubrics.yaml"
    if not rubrics_file.exists():
        rubrics_file = td / "rubrics.draft.yaml"
    rubrics_list = []
    if rubrics_file.exists():
        try:
            rubrics_list = yaml.safe_load(
                rubrics_file.read_text(encoding="utf-8")) or []
        except Exception:
            rubrics_list = []

    for act in actions:
        t = act.get("type", "")
        if t == "update_rubric":
            rid = act.get("rubric_id", "")
            field = act.get("field", "")
            value = act.get("value")
            for r in rubrics_list:
                if isinstance(r, dict) and r.get("id") == rid and field:
                    r[field] = value
                    applied_count += 1
        elif t == "delete_rubric":
            rid = act.get("rubric_id", "")
            before = len(rubrics_list)
            rubrics_list = [r for r in rubrics_list
                           if not (isinstance(r, dict) and r.get("id") == rid)]
            if len(rubrics_list) < before:
                applied_count += 1
        elif t == "add_rubric":
            new_r = act.get("rubric", {})
            if new_r.get("id"):
                rubrics_list.append(new_r)
                applied_count += 1
        elif t in ("update_script", "add_probe", "delete_probe"):
            sid = act.get("script_id", "")
            for d in [td / "personas_draft", td / "personas"]:
                sf = d / f"{sid}.yaml"
                if sf.exists():
                    try:
                        sd = yaml.safe_load(
                            sf.read_text(encoding="utf-8")) or {}
                        if t == "update_script":
                            sd[act.get("field", "")] = act.get("value")
                        elif t == "add_probe":
                            sd.setdefault("probes", []).append(act["probe"])
                        elif t == "delete_probe":
                            pid = act.get("probe_id", "")
                            sd["probes"] = [p for p in sd.get("probes", [])
                                           if p.get("id") != pid]
                        sf.write_text(
                            yaml.safe_dump(sd, allow_unicode=True,
                                          sort_keys=False),
                            encoding="utf-8")
                        applied_count += 1
                    except Exception:
                        pass

    # 写回 rubrics
    if any(a.get("type", "").endswith("rubric") for a in actions):
        rubrics_file.write_text(
            yaml.safe_dump(rubrics_list, allow_unicode=True,
                          sort_keys=False),
            encoding="utf-8")

    return {
        "reply": reply + (f"\n\n(已应用 {applied_count} 项修改)" if applied_count else ""),
        "applied": applied_count > 0,
    }
