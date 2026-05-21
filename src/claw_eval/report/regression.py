"""回归对比 —— 两次评测结果的差异分析。

输入:两批 GradingResult(分别属一个 run_id)
输出:RegressionReport,含 rubric / persona / 维度三层 diff,
      可终端输出 + JSON 落盘 + dashboard 渲染。

显著性阈值默认 0.05(经验值,过低则被采样噪音淹没)。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..models.trace import GradingResult
from .aggregate import aggregate


@dataclass
class RubricDiff:
    rubric_id: str
    dimension: str
    old_avg: float | None
    new_avg: float | None
    delta: float | None
    significance: str          # improve / regress / flat / added / removed
    old_n: int
    new_n: int


@dataclass
class PersonaDiff:
    persona_id: str
    old_n: int
    new_n: int
    old_pass_rate: float | None
    new_pass_rate: float | None
    delta_pass_rate: float | None
    old_completion: float | None
    new_completion: float | None


@dataclass
class RegressionReport:
    task_id: str
    old_label: str
    new_label: str
    old_total: int
    new_total: int
    old_pass_rate: float
    new_pass_rate: float
    old_score_avg: float
    new_score_avg: float
    by_dimension: list[tuple[str, float, float, float]] = field(default_factory=list)
    by_rubric: list[RubricDiff] = field(default_factory=list)
    by_persona: list[PersonaDiff] = field(default_factory=list)
    n_improvements: int = 0
    n_regressions: int = 0
    threshold: float = 0.05


# ============================== 计算 ==============================

def _classify(delta: float, threshold: float) -> str:
    if abs(delta) < threshold:
        return "flat"
    return "improve" if delta > 0 else "regress"


def compute_regression(old_results: list[GradingResult],
                       new_results: list[GradingResult],
                       task_id: str,
                       old_label: str = "old",
                       new_label: str = "new",
                       threshold: float = 0.05) -> RegressionReport:
    """两批结果(过滤同 task_id)的差异。"""
    old = [r for r in old_results if r.task_id == task_id]
    new = [r for r in new_results if r.task_id == task_id]

    old_sum = aggregate(old)
    new_sum = aggregate(new)

    rep = RegressionReport(
        task_id=task_id,
        old_label=old_label, new_label=new_label,
        old_total=len(old), new_total=len(new),
        old_pass_rate=old_sum.pass_rate,
        new_pass_rate=new_sum.pass_rate,
        old_score_avg=(sum(r.task_score for r in old) / len(old)) if old else 0.0,
        new_score_avg=(sum(r.task_score for r in new) / len(new)) if new else 0.0,
        threshold=threshold,
    )

    # 维度
    for dim in ("completion", "robustness", "safety"):
        o = getattr(old_sum, f"avg_{dim}")
        n = getattr(new_sum, f"avg_{dim}")
        rep.by_dimension.append((dim, round(o, 4), round(n, 4), round(n - o, 4)))

    # Rubric(取并集)
    all_rubrics = sorted(set(old_sum.by_rubric) | set(new_sum.by_rubric))
    for rid in all_rubrics:
        ob = old_sum.by_rubric.get(rid)
        nb = new_sum.by_rubric.get(rid)
        oa = ob["avg_score"] if ob else None
        na = nb["avg_score"] if nb else None
        on = ob["n"] if ob else 0
        nn = nb["n"] if nb else 0
        dim = (ob or nb).get("dimension", "")

        if oa is None and na is not None:
            sig, delta = "added", None
        elif na is None and oa is not None:
            sig, delta = "removed", None
        else:
            delta = round(na - oa, 4)
            sig = _classify(delta, threshold)
            if sig == "improve":
                rep.n_improvements += 1
            elif sig == "regress":
                rep.n_regressions += 1

        rep.by_rubric.append(RubricDiff(
            rubric_id=rid, dimension=dim,
            old_avg=oa, new_avg=na, delta=delta,
            significance=sig, old_n=on, new_n=nn,
        ))

    # Persona(取并集)
    for pid in sorted(set(old_sum.by_persona) | set(new_sum.by_persona)):
        ob = old_sum.by_persona.get(pid, {})
        nb = new_sum.by_persona.get(pid, {})
        opr = ob.get("pass_rate")
        npr = nb.get("pass_rate")
        rep.by_persona.append(PersonaDiff(
            persona_id=pid,
            old_n=ob.get("n", 0), new_n=nb.get("n", 0),
            old_pass_rate=opr, new_pass_rate=npr,
            delta_pass_rate=(round(npr - opr, 4)
                             if opr is not None and npr is not None else None),
            old_completion=ob.get("completion"),
            new_completion=nb.get("completion"),
        ))

    return rep


# ============================== 终端格式 ==============================

def _arrow(delta: float | None, threshold: float = 0.05) -> str:
    if delta is None:
        return "(无)"
    sign = "+" if delta >= 0 else ""
    txt = f"{sign}{delta:.2f}"
    if abs(delta) < threshold:
        return txt
    if abs(delta) > 0.20:
        return f"{txt} {'↑↑↑' if delta > 0 else '↓↓↓'}"
    return f"{txt} {'↑' if delta > 0 else '↓'}"


def format_regression_terminal(rep: RegressionReport) -> str:
    L: list[str] = []
    L.append(f"\n═══ 回归对比 · {rep.task_id} · {rep.old_label} → {rep.new_label} ═══\n")
    L.append("总览:")
    L.append(f"  result 数         {rep.old_total} → {rep.new_total}")
    L.append(f"  task_score 平均   {rep.old_score_avg:.4f} → "
             f"{rep.new_score_avg:.4f}    "
             f"{_arrow(rep.new_score_avg - rep.old_score_avg, rep.threshold)}")
    L.append(f"  通过率           "
             f"{rep.old_pass_rate * 100:>3.0f}% → "
             f"{rep.new_pass_rate * 100:>3.0f}%    "
             f"{_arrow(rep.new_pass_rate - rep.old_pass_rate, rep.threshold)}")

    L.append("\n按维度:")
    for dim, ov, nv, dlt in rep.by_dimension:
        L.append(f"  {dim:<10}   {ov:.2f} → {nv:.2f}    {_arrow(dlt, rep.threshold)}")

    L.append(f"\n按 Rubric(只显示 ≥{rep.threshold} 变化):")
    has = False
    for rd in rep.by_rubric:
        if rd.significance in ("flat",):
            continue
        has = True
        os_ = f"{rd.old_avg:.2f}" if rd.old_avg is not None else "  — "
        ns_ = f"{rd.new_avg:.2f}" if rd.new_avg is not None else "  — "
        if rd.significance == "added":
            L.append(f"  + {rd.rubric_id:<30}     — → {ns_}    (新 rubric)")
        elif rd.significance == "removed":
            L.append(f"  - {rd.rubric_id:<30}   {os_} →   —      (旧 rubric 已删)")
        else:
            L.append(f"  {rd.rubric_id:<30}   {os_} → {ns_}    "
                     f"{_arrow(rd.delta, rep.threshold)}")
    if not has:
        L.append("  (无显著变化)")

    L.append(f"\n按 Persona(通过率显著变化):")
    has = False
    for pd in rep.by_persona:
        if pd.delta_pass_rate is None or abs(pd.delta_pass_rate) < rep.threshold:
            continue
        has = True
        opr = f"{pd.old_pass_rate * 100:>3.0f}%"
        npr = f"{pd.new_pass_rate * 100:>3.0f}%"
        L.append(f"  {pd.persona_id:<25}   {opr} → {npr}    "
                 f"{_arrow(pd.delta_pass_rate, rep.threshold)}")
    if not has:
        L.append("  (无显著变化)")

    L.append(f"\n汇总:{rep.n_improvements} 改进 / {rep.n_regressions} 退化 "
             f"(阈值 {rep.threshold})")
    return "\n".join(L)


# ============================== JSON 保存 ==============================

def report_to_dict(rep: RegressionReport) -> dict[str, Any]:
    return asdict(rep)


def save_regression(rep: RegressionReport, path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(report_to_dict(rep), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
