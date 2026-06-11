"""Meta-Eval(人工校准)endpoints。

流程:抽样 → 前端逐条标注 → 校准报告。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..meta_eval import (
    append_annotation,
    collect_judge_scores,
    compute_calibration,
    load_annotations,
    load_samples,
    save_samples,
    stratified_sample,
)


def _root() -> Path:
    cur = Path(__file__).resolve()
    for p in [cur, *cur.parents]:
        if (p / "pyproject.toml").exists():
            return p
    return Path.cwd()


ROOT = _root()
router = APIRouter()


class SampleRequest(BaseModel):
    n: int = 30
    run_id: str | None = None     # 限定某次测试;不传 = 全部历史
    seed: int = 42


class AnnotationRequest(BaseModel):
    item_id: str
    agree: bool
    human_score: float | None = None     # agree=False 时必填
    comment: str = ""
    annotator: str = ""


@router.post("/tasks/{task_id}/meta-eval/sample")
def create_samples(task_id: str, req: SampleRequest):
    """从已有评分结果分层抽样,生成标注任务清单。"""
    items = collect_judge_scores(ROOT / "traces", task_id=task_id,
                                 run_id=req.run_id)
    if not items:
        raise HTTPException(
            422, "没有可抽样的 LLM Judge 评分(先跑一次带 Judge 的测试)")
    samples = stratified_sample(items, n=req.n, seed=req.seed)
    save_samples(ROOT, task_id, samples)
    return {"n_pool": len(items), "n_sampled": len(samples)}


@router.get("/tasks/{task_id}/meta-eval/samples")
def list_samples(task_id: str):
    """标注任务清单 + 各自标注状态。"""
    samples = load_samples(ROOT, task_id)
    anns = {a["item_id"]: a for a in load_annotations(ROOT, task_id)}
    out = []
    for s in samples:
        a = anns.get(s["item_id"])
        out.append({**s, "annotated": a is not None,
                    "annotation": a})
    return {"samples": out,
            "n_total": len(out),
            "n_annotated": sum(1 for s in out if s["annotated"])}


@router.get("/tasks/{task_id}/meta-eval/items/{item_id:path}/conversation")
def get_item_conversation(task_id: str, item_id: str):
    """读某条标注任务对应的对话内容(从 trace JSONL)。"""
    samples = load_samples(ROOT, task_id)
    sample = next((s for s in samples if s["item_id"] == item_id), None)
    if sample is None:
        raise HTTPException(404, f"标注任务 {item_id} 不存在")

    trace_path = sample.get("trace_path") or ""
    tp = Path(trace_path)
    if not tp.is_absolute():
        tp = ROOT / trace_path
    # trace_path 可能失效(旧数据迁移),按 item_id 回退推断
    if not tp.exists():
        run_and_stem = item_id.split("#")[0]      # "<run>/<stem>"
        tp = ROOT / "traces" / f"{run_and_stem}.jsonl"
    if not tp.exists():
        raise HTTPException(404, f"trace 文件不存在:{tp}")

    import json
    turns = []
    for line in tp.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if ev.get("event") == "turn":
            turns.append({
                "turn": ev.get("turn"),
                "role": ev.get("role"),
                "text": ev.get("text", ""),
                "is_probe": ev.get("is_probe", False),
            })
    return {"item": sample, "turns": turns}


@router.post("/tasks/{task_id}/meta-eval/annotations")
def submit_annotation(task_id: str, req: AnnotationRequest):
    """提交一条人工标注。"""
    if not req.agree and req.human_score is None:
        raise HTTPException(422, "不同意 Judge 评分时必须给出人工分数")
    append_annotation(ROOT, task_id, {
        "item_id": req.item_id,
        "agree": req.agree,
        "human_score": req.human_score,
        "comment": req.comment,
        "annotator": req.annotator,
        "ts": datetime.now().isoformat(timespec="seconds"),
    })
    return {"ok": True}


@router.get("/tasks/{task_id}/meta-eval/report")
def calibration_report(task_id: str):
    """人机一致率校准报告。"""
    samples = load_samples(ROOT, task_id)
    anns = load_annotations(ROOT, task_id)
    rep = compute_calibration(samples, anns)
    return rep.to_dict()
