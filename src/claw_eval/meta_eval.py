"""Meta-Eval —— 人工校准评测系统本身(T6)。

回答「自动评分可信吗」:
1. 分层抽样 rubric 级评分(只抽 LLM Judge 评的,规则 matcher 是确定性的)
2. 人工标注:同意 Judge 评分 / 不同意并给人工分
3. 算一致率:总体 + 按 rubric + 系统性偏差(Judge 偏松/偏严)

产物落 meta_eval/ 目录:
  samples_<task_id>.json       —— 抽样出的标注任务
  annotations_<task_id>.jsonl  —— 人工标注(append-only,可多人)
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

# 人机评分差 ≤ AGREE_TOLERANCE 算一致
AGREE_TOLERANCE = 0.2


# ------------------------------------------------------------------
# 抽样
# ------------------------------------------------------------------

@dataclass
class AnnotationItem:
    """一条待标注任务 = 一次 rubric 级 LLM Judge 评分。"""
    item_id: str
    task_id: str
    run_id: str
    trace_path: str
    persona_id: str
    script_id: str
    rubric_id: str
    dimension: str
    judge_score: float
    judge_reasoning: str
    evidence_turn: int | None
    rubric_check: str = ""
    input_hash: str = ""
    judge_config: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "trace_path": self.trace_path,
            "persona_id": self.persona_id,
            "script_id": self.script_id,
            "rubric_id": self.rubric_id,
            "dimension": self.dimension,
            "judge_score": self.judge_score,
            "judge_reasoning": self.judge_reasoning,
            "evidence_turn": self.evidence_turn,
            "rubric_check": self.rubric_check,
            "input_hash": self.input_hash,
            "judge_config": self.judge_config,
        }


def collect_judge_scores(traces_dir: Path, task_id: str | None = None,
                         run_id: str | None = None) -> list[AnnotationItem]:
    """扫 traces 下全部 result.json,收集 LLM Judge 评的 rubric 分。

    只收 method == 'llm_judge' 的(规则 matcher 确定性,无需人审)。
    """
    items: list[AnnotationItem] = []
    if not traces_dir.exists():
        return items
    if run_id:
        dirs = [traces_dir / run_id]
    else:
        dirs = [d for d in sorted(traces_dir.iterdir()) if d.is_dir()]

    for d in dirs:
        if not d.is_dir():
            continue
        checks, input_hash, judge_config = {}, "", {}
        if (d / "manifest.json").exists():
            from .runs import load_manifest
            from .models.task import TaskDefinition
            from .models.rubric import load_rubrics
            from .templating import render_template
            manifest = load_manifest(traces_dir.parent, d.name)
            frozen = d / "inputs/tasks" / manifest["task_id"]
            task = TaskDefinition.from_yaml(frozen / "task.yaml")
            checks = {r.id: render_template(r.check, task.variables) +
                      (f"\n适用条件: {r.trigger.model_dump()}" if r.trigger else "")
                      for r in load_rubrics(frozen / "rubrics.yaml")}
            input_hash = manifest["input_hash"]
            judge_config = json.loads((d / "inputs/models.json").read_text()).get("judge", {})
        for rf in sorted(d.glob("*.result.json")):
            try:
                data = json.loads(rf.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if task_id and data.get("task_id") != task_id:
                continue
            for rs in data.get("rubric_scores", []):
                if rs.get("method") != "llm_judge":
                    continue
                if rs.get("status", "scored") != "scored" or not rs.get("triggered", True):
                    continue
                rid = rs["rubric_id"]
                items.append(AnnotationItem(
                    item_id=f"{d.name}/{rf.stem.replace('.result', '')}#{rid}",
                    task_id=data.get("task_id", ""),
                    run_id=d.name,
                    trace_path=data.get("trace_path") or "",
                    persona_id=data.get("persona_id", ""),
                    script_id=data.get("script_id", "") or data.get("persona_id", ""),
                    rubric_id=rid,
                    dimension=rs.get("dimension", ""),
                    judge_score=rs.get("score", 0.0),
                    judge_reasoning=rs.get("reasoning", ""),
                    evidence_turn=rs.get("evidence_turn"),
                    rubric_check=checks.get(rid, ""), input_hash=input_hash, judge_config=judge_config,
                ))
    return items


def stratified_sample(items: list[AnnotationItem], n: int,
                      seed: int = 42) -> list[AnnotationItem]:
    """分层抽样:每个 rubric 轮流抽,且每个 rubric 内部高分/低分交替。

    样本数足够时覆盖各 rubric；不足时随机选择 rubric，返回实际覆盖情况。
    """
    rng = random.Random(seed)
    by_rubric: dict[str, list[AnnotationItem]] = {}
    for it in items:
        by_rubric.setdefault(it.rubric_id, []).append(it)

    # 每个 rubric 内部:按分数排序后,从两端向中间交替取(高/低/高/低…)
    queues: dict[str, list[AnnotationItem]] = {}
    for rid, lst in by_rubric.items():
        lst = sorted(lst, key=lambda x: x.judge_score)
        rng.shuffle(lst)  # 同分内随机
        lst.sort(key=lambda x: x.judge_score)
        alt: list[AnnotationItem] = []
        lo, hi = 0, len(lst) - 1
        take_low = True
        while lo <= hi:
            if take_low:
                alt.append(lst[lo]); lo += 1
            else:
                alt.append(lst[hi]); hi -= 1
            take_low = not take_low
        queues[rid] = alt

    # round-robin 各 rubric
    out: list[AnnotationItem] = []
    rubric_ids = sorted(queues.keys())
    if n < len(rubric_ids):
        rng.shuffle(rubric_ids)
    while len(out) < n and any(queues[r] for r in rubric_ids):
        for rid in rubric_ids:
            if len(out) >= n:
                break
            if queues[rid]:
                out.append(queues[rid].pop(0))
    return out


# ------------------------------------------------------------------
# 标注存储
# ------------------------------------------------------------------

def meta_eval_dir(root: Path) -> Path:
    d = root / "meta_eval"
    d.mkdir(exist_ok=True)
    return d


def _batch_dir(root: Path, task_id: str) -> Path:
    from .runs import validate_id
    validate_id(task_id)
    return meta_eval_dir(root) / task_id / "batches"


def current_batch(root: Path, task_id: str) -> str | None:
    pointer = _batch_dir(root, task_id).parent / "current.json"
    return json.loads(pointer.read_text())["batch_id"] if pointer.exists() else None


def load_batch(root: Path, task_id: str, batch_id: str | None = None) -> dict:
    batch_id = batch_id or current_batch(root, task_id)
    if batch_id:
        from .runs import validate_id
        validate_id(batch_id)
        return json.loads((_batch_dir(root, task_id) / f"{batch_id}.json").read_text(encoding="utf-8"))
    legacy = meta_eval_dir(root) / f"samples_{task_id}.json"
    return {"batch_id": None, "mode": "assisted", "samples": json.loads(legacy.read_text()) if legacy.exists() else []}


def list_batches(root: Path, task_id: str) -> list[dict]:
    rows = []
    for path in sorted(_batch_dir(root, task_id).glob("*.json"), reverse=True):
        data = json.loads(path.read_text(encoding="utf-8"))
        rows.append({k: v for k, v in data.items() if k != "samples"})
    return rows


def save_samples(root: Path, task_id: str, samples: list[AnnotationItem], *, metadata: dict | None = None) -> Path:
    from datetime import datetime, timezone
    from uuid import uuid4
    from .runs import atomic_json
    batch_id = "sample_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid4().hex[:6]
    directory = _batch_dir(root, task_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{batch_id}.json"
    atomic_json(path, {"batch_id": batch_id, "created_at": datetime.now(timezone.utc).isoformat(),
                       "mode": "assisted", **(metadata or {}), "n_samples": len(samples),
                       "samples": [s.to_dict() for s in samples]})
    atomic_json(directory.parent / "current.json", {"batch_id": batch_id})
    return path


def load_samples(root: Path, task_id: str, batch_id: str | None = None) -> list[dict]:
    return load_batch(root, task_id, batch_id)["samples"]


def _annotation_file(root: Path, task_id: str, batch_id: str | None) -> Path:
    from .runs import validate_id
    validate_id(task_id)
    batch_id = batch_id or current_batch(root, task_id)
    if batch_id:
        validate_id(batch_id)
        return _batch_dir(root, task_id) / f"{batch_id}.annotations.jsonl"
    return meta_eval_dir(root) / f"annotations_{task_id}.jsonl"


def append_annotation(root: Path, task_id: str, ann: dict, batch_id: str | None = None) -> None:
    import fcntl
    path = _annotation_file(root, task_id, batch_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(ann, ensure_ascii=False, allow_nan=False) + "\n")
        f.flush()
        fcntl.flock(f, fcntl.LOCK_UN)


def load_annotations(root: Path, task_id: str, batch_id: str | None = None) -> list[dict]:
    path = _annotation_file(root, task_id, batch_id)
    if not path.exists():
        return []
    dedup = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        dedup[(data["item_id"], data.get("annotator", ""))] = data
    return list(dedup.values())


# ------------------------------------------------------------------
# 校准计算
# ------------------------------------------------------------------

@dataclass
class CalibrationReport:
    n_samples: int = 0
    n_annotated: int = 0
    n_ratings: int = 0
    independent_ratings: int = 0
    sufficient_sample: bool = False
    annotator_disagreements: list[dict] = field(default_factory=list)
    agreement_rate: float = 0.0          # |judge - human| <= 0.2 的比例
    mean_bias: float = 0.0               # mean(judge - human),>0 = Judge 偏松
    by_rubric: dict[str, dict[str, Any]] = field(default_factory=dict)
    disagreements: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def compute_calibration(samples: list[dict],
                        annotations: list[dict]) -> CalibrationReport:
    """人机一致率 + 按 rubric 细分 + 系统性偏差。"""
    rep = CalibrationReport(n_samples=len(samples))
    ann_by_id = {(a["item_id"], a.get("annotator", "")): a for a in annotations if "item_id" in a}
    human_by_item = {}
    independent_items = set()
    seen_items = set()
    sample_by_id = {s["item_id"]: s for s in samples}

    diffs: list[float] = []
    agrees: list[bool] = []
    by_rubric: dict[str, dict] = {}

    for (item_id, annotator), ann in ann_by_id.items():
        s = sample_by_id.get(item_id)
        if s is None:
            continue
        judge = float(s["judge_score"])
        # 「同意」= 人工分即 judge 分;不同意则用人工给的分
        if ann.get("agree"):
            human = judge
        else:
            human = float(ann.get("human_score", 0.0))
        import math
        if not math.isfinite(human) or not 0 <= human <= 1:
            raise ValueError("人工分必须是 0～1 的有限数值")
        seen_items.add(item_id)
        human_by_item.setdefault(item_id, []).append({"annotator": annotator, "score": human})
        if ann.get("mode") == "independent":
            rep.independent_ratings += 1
            independent_items.add(item_id)
        diff = judge - human
        agree = abs(diff) <= AGREE_TOLERANCE
        diffs.append(diff)
        agrees.append(agree)

        rid = s["rubric_id"]
        br = by_rubric.setdefault(rid, {
            "n": 0, "agree_n": 0, "bias_sum": 0.0, "items": set()})
        br["n"] += 1
        br["items"].add(item_id)
        br["agree_n"] += int(agree)
        br["bias_sum"] += diff

        if not agree:
            rep.disagreements.append({
                "item_id": item_id,
                "rubric_id": rid,
                "judge_score": judge,
                "human_score": human,
                "judge_reasoning": s.get("judge_reasoning", ""),
                "comment": ann.get("comment", ""),
            })

    rep.n_annotated = len(seen_items)
    rep.n_ratings = len(agrees)
    rep.sufficient_sample = len(independent_items) >= 20
    rep.annotator_disagreements = [{"item_id": key, "ratings": rows}
                                   for key, rows in human_by_item.items()
                                   if len(rows) > 1 and max(r["score"] for r in rows) - min(r["score"] for r in rows) > AGREE_TOLERANCE]
    if agrees:
        rep.agreement_rate = round(sum(agrees) / len(agrees), 4)
        rep.mean_bias = round(sum(diffs) / len(diffs), 4)
    for rid, br in by_rubric.items():
        n = br["n"]
        rep.by_rubric[rid] = {
            "n": n,
            "n_items": len(br["items"]),
            "sufficient_sample": len(br["items"] & independent_items) >= 5,
            "agreement_rate": round(br["agree_n"] / n, 4),
            "mean_bias": round(br["bias_sum"] / n, 4),
        }
    return rep
