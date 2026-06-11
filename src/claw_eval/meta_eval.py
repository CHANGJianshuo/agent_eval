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
from dataclasses import dataclass, field
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
        }


def collect_judge_scores(traces_dir: Path, task_id: str | None = None,
                         run_id: str | None = None) -> list[AnnotationItem]:
    """扫 traces 下全部 result.json,收集 LLM Judge 评的 rubric 分。

    只收 method == 'llm_judge' 的(规则 matcher 确定性,无需人审)。
    """
    items: list[AnnotationItem] = []
    if run_id:
        dirs = [traces_dir / run_id]
    else:
        dirs = [d for d in sorted(traces_dir.iterdir()) if d.is_dir()]

    for d in dirs:
        if not d.is_dir():
            continue
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
                if not rs.get("triggered", True):
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
                ))
    return items


def stratified_sample(items: list[AnnotationItem], n: int,
                      seed: int = 42) -> list[AnnotationItem]:
    """分层抽样:每个 rubric 轮流抽,且每个 rubric 内部高分/低分交替。

    保证:1) 全部 rubric 都覆盖;2) 不只抽简单 case。
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


def save_samples(root: Path, task_id: str,
                 samples: list[AnnotationItem]) -> Path:
    p = meta_eval_dir(root) / f"samples_{task_id}.json"
    p.write_text(json.dumps([s.to_dict() for s in samples],
                            ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load_samples(root: Path, task_id: str) -> list[dict]:
    p = meta_eval_dir(root) / f"samples_{task_id}.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def append_annotation(root: Path, task_id: str, ann: dict) -> None:
    """追加一条人工标注。ann 须含 item_id / agree / human_score。"""
    p = meta_eval_dir(root) / f"annotations_{task_id}.jsonl"
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(ann, ensure_ascii=False) + "\n")


def load_annotations(root: Path, task_id: str) -> list[dict]:
    p = meta_eval_dir(root) / f"annotations_{task_id}.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    # 同 item 多次标注,保留最后一条
    dedup: dict[str, dict] = {}
    for a in out:
        dedup[a.get("item_id", "")] = a
    return list(dedup.values())


# ------------------------------------------------------------------
# 校准计算
# ------------------------------------------------------------------

@dataclass
class CalibrationReport:
    n_samples: int = 0
    n_annotated: int = 0
    agreement_rate: float = 0.0          # |judge - human| <= 0.2 的比例
    mean_bias: float = 0.0               # mean(judge - human),>0 = Judge 偏松
    by_rubric: dict[str, dict[str, Any]] = field(default_factory=dict)
    disagreements: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "n_samples": self.n_samples,
            "n_annotated": self.n_annotated,
            "agreement_rate": self.agreement_rate,
            "mean_bias": self.mean_bias,
            "by_rubric": self.by_rubric,
            "disagreements": self.disagreements,
        }


def compute_calibration(samples: list[dict],
                        annotations: list[dict]) -> CalibrationReport:
    """人机一致率 + 按 rubric 细分 + 系统性偏差。"""
    rep = CalibrationReport(n_samples=len(samples))
    ann_by_id = {a["item_id"]: a for a in annotations if "item_id" in a}
    sample_by_id = {s["item_id"]: s for s in samples}

    diffs: list[float] = []
    agrees: list[bool] = []
    by_rubric: dict[str, dict] = {}

    for item_id, ann in ann_by_id.items():
        s = sample_by_id.get(item_id)
        if s is None:
            continue
        judge = float(s["judge_score"])
        # 「同意」= 人工分即 judge 分;不同意则用人工给的分
        if ann.get("agree"):
            human = judge
        else:
            human = float(ann.get("human_score", 0.0))
        diff = judge - human
        agree = abs(diff) <= AGREE_TOLERANCE
        diffs.append(diff)
        agrees.append(agree)

        rid = s["rubric_id"]
        br = by_rubric.setdefault(rid, {
            "n": 0, "agree_n": 0, "bias_sum": 0.0})
        br["n"] += 1
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

    rep.n_annotated = len(agrees)
    if agrees:
        rep.agreement_rate = round(sum(agrees) / len(agrees), 4)
        rep.mean_bias = round(sum(diffs) / len(diffs), 4)
    for rid, br in by_rubric.items():
        n = br["n"]
        rep.by_rubric[rid] = {
            "n": n,
            "agreement_rate": round(br["agree_n"] / n, 4),
            "mean_bias": round(br["bias_sum"] / n, 4),
        }
    return rep
