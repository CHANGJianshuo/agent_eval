"""把多份 GradingResult 聚合成可视化用的总结结构。

三视角:按维度、按 persona、按 rubric;另出 Persona × Rubric 热力图数据。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models.trace import GradingResult


@dataclass
class AggregateSummary:
    total_runs: int = 0
    evaluated_runs: int = 0
    incomplete_runs: int = 0
    error_runs: int = 0
    pass_count: int = 0
    pass_rate: float = 0.0
    avg_completion: float = 0.0
    avg_robustness: float = 0.0
    avg_safety: float = 0.0
    by_persona: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_rubric: dict[str, dict[str, Any]] = field(default_factory=dict)
    heatmap: dict[str, dict[str, float]] = field(default_factory=dict)
    runs: list[dict[str, Any]] = field(default_factory=list)
    # 全部出现过的 rubric_id 顺序(按首次出现),用于热力图列序
    rubric_order: list[str] = field(default_factory=list)
    rubric_dim: dict[str, str] = field(default_factory=dict)


def _round(x: float, n: int = 4) -> float:
    return round(x, n)


def _row_key(r: GradingResult) -> str:
    """热力图/分组行键:优先剧本 script_id,旧数据回退 persona_id。"""
    return getattr(r, "script_id", "") or r.persona_id or "(unknown)"


def aggregate(results: list[GradingResult]) -> AggregateSummary:
    """计算总览 / 按剧本 / 按 rubric / 热力图。

    行分组优先用 script_id(剧本=场景路径);旧数据无 script_id 回退 persona_id。
    """
    s = AggregateSummary()
    s.total_runs = len(results)
    if s.total_runs == 0:
        return s

    evaluated = [r for r in results if r.status == "complete"]
    s.evaluated_runs = len(evaluated)
    s.incomplete_runs = sum(r.status == "incomplete" for r in results)
    s.error_runs = sum(r.status == "error" for r in results)
    s.pass_count = sum(r.passed is True for r in evaluated)
    if evaluated:
        s.pass_rate = _round(s.pass_count / len(evaluated))
        for dim in ("completion", "robustness", "safety"):
            setattr(s, f"avg_{dim}", _round(sum(getattr(r.dimension_scores, dim)
                                               for r in evaluated) / len(evaluated)))

    # 按剧本汇总(by_persona 字段名保留兼容,内容已是剧本粒度)
    for r in results:
        pid = _row_key(r)
        bp = s.by_persona.setdefault(pid, {
            "n": 0, "evaluated_n": 0, "pass_n": 0,
            "completion_sum": 0.0, "robustness_sum": 0.0, "safety_sum": 0.0,
        })
        bp["n"] += 1
        if r.status != "complete":
            continue
        bp["evaluated_n"] += 1
        if r.passed:
            bp["pass_n"] += 1
        bp["completion_sum"] += r.dimension_scores.completion
        bp["robustness_sum"] += r.dimension_scores.robustness
        bp["safety_sum"] += r.dimension_scores.safety
    for bp in s.by_persona.values():
        n = bp["evaluated_n"] or 1
        bp["pass_rate"] = _round(bp["pass_n"] / n)
        bp["completion"] = _round(bp["completion_sum"] / n)
        bp["robustness"] = _round(bp["robustness_sum"] / n)
        bp["safety"] = _round(bp["safety_sum"] / n)

    # 按 rubric 汇总 + 记录 rubric 顺序与维度
    seen: set[str] = set()
    for r in results:
        for rs in r.rubric_scores:
            if rs.rubric_id not in seen:
                seen.add(rs.rubric_id)
                s.rubric_order.append(rs.rubric_id)
                s.rubric_dim[rs.rubric_id] = rs.dimension
            if rs.status != "scored":
                continue
            br = s.by_rubric.setdefault(rs.rubric_id, {
                "n": 0, "score_sum": 0.0, "dimension": rs.dimension,
            })
            br["n"] += 1
            br["score_sum"] += rs.score
    for br in s.by_rubric.values():
        br["avg_score"] = _round(br["score_sum"] / br["n"])

    # 剧本 × Rubric 热力图。先累计 sum/count，最后一次性求均值；
    # 不能用 ``(旧均值 + 新值) / 2``，否则第 3 次及之后的 trial 权重会失真。
    heatmap_totals: dict[str, dict[str, dict[str, float]]] = {}
    for r in results:
        pid = _row_key(r)
        row = heatmap_totals.setdefault(pid, {})
        for rs in r.rubric_scores:
            if rs.status != "scored":
                continue
            cell = row.setdefault(rs.rubric_id, {"sum": 0.0, "n": 0.0})
            cell["sum"] += rs.score
            cell["n"] += 1.0
    for pid, row in heatmap_totals.items():
        s.heatmap[pid] = {
            rid: _round(cell["sum"] / cell["n"])
            for rid, cell in row.items()
        }

    # runs 列表(用于报告中「每条运行」表格)
    for r in results:
        s.runs.append({
            "status": r.status,
            "task_id": r.task_id,
            "persona_id": r.persona_id,
            "script_id": _row_key(r),
            "task_score": r.task_score,
            "passed": r.passed,
            "completion": r.dimension_scores.completion,
            "robustness": r.dimension_scores.robustness,
            "safety": r.dimension_scores.safety,
            "trace_path": r.trace_path,
            "error_message": r.error_message,
        })
    return s


def load_results_dir(traces_dir: str | Path,
                     recursive: bool = True) -> list[GradingResult]:
    """读 traces_dir 下所有 *.result.json,返回 GradingResult 列表。

    recursive=True(默认)→ 递归扫子目录(traces/<run_id>/*.result.json)。
    既支持新的「run_id 子目录」布局,也兼容旧的扁平布局。
    """
    directory = Path(traces_dir)
    if not directory.exists():
        return []
    if recursive:
        dirs = {directory}
        dirs.update(p.parent for p in directory.rglob("*.result.json"))
        dirs.update(p.parent for p in directory.rglob("cases.json")
                    if (p.parent / "manifest.json").exists())
        return [r for d in sorted(dirs) for r in load_results_dir(d, recursive=False)]

    from ..models.trace import DimensionScores
    from ..runs import load_manifest
    manifest = None
    cases = []
    if (directory / "manifest.json").exists():
        manifest = load_manifest(directory.parent.parent, directory.name)
        if manifest.get("cases_hash"):
            cases = json.loads((directory / "cases.json").read_text(encoding="utf-8"))
    outcome_file = directory / "outcome.json"
    outcome = json.loads(outcome_file.read_text(encoding="utf-8")) if outcome_file.exists() else {}
    planned = {f"{manifest['task_id']}_{c['name']}_t{c['trial_idx'] + 1}": c for c in cases}
    files = {p.name.removesuffix('.result.json'): p for p in directory.glob("*.result.json")}
    out = []
    for stem in sorted(set(planned) | set(files)):
        path = files.get(stem)
        failure = ""
        result = None
        if path:
            try:
                result = GradingResult.model_validate_json(path.read_text(encoding="utf-8"))
                if manifest and (result.task_id != manifest['task_id'] or result.run_id != directory.name
                                 or result.input_hash != manifest['input_hash'] or stem not in planned):
                    raise ValueError("评分结果与运行输入或用例清单不一致")
            except Exception as exc:
                if not manifest:
                    raise ValueError(f"结果文件损坏: {path.name}: {exc}") from exc
                failure = f"结果文件损坏: {path.name}: {exc}"
                result = None
        if result is None:
            case = planned.get(stem, {})
            persona = case.get("persona", {})
            if not failure:
                prefix = f"{case.get('name', '')} t{case.get('trial_idx', 0) + 1}:"
                failure = next((f for f in outcome.get("failures", []) if f.startswith(prefix)), "")
            ended = bool(outcome) or bool(failure)
            result = GradingResult(
                task_id=manifest["task_id"], run_id=directory.name,
                input_hash=manifest["input_hash"], persona_id=persona.get("id", case.get("name", stem)),
                script_id=persona.get("script_id", "") or persona.get("id", ""),
                demographics=persona.get("demographics", {}),
                dimension_scores=DimensionScores(), task_score=None, passed=None,
                status="error" if ended else "incomplete",
                error_message=failure or ("未产生评分结果，请查看运行日志" if ended else "尚未完成评分"),
                trace_path=str(directory / f"{stem}.jsonl"),
            )
        result.case_id = stem
        out.append(result)
    return out
