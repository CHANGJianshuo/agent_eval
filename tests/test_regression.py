"""回归对比单测 —— compute / classify / 终端格式 / JSON 保存。"""
from __future__ import annotations

import json
from pathlib import Path

from claw_eval.models.trace import (
    DimensionScores,
    GradingResult,
    RubricScore,
)
from claw_eval.report.regression import (
    _arrow,
    _classify,
    compute_regression,
    format_regression_terminal,
    save_regression,
)


# ----------------------------- helpers -----------------------------

def _result(task_id: str, persona: str, score: float, passed: bool,
            rubric_scores: list[tuple[str, str, float, bool]],
            dim_completion: float = 0.5,
            dim_robustness: float = 0.5,
            dim_safety: float = 1.0) -> GradingResult:
    return GradingResult(
        task_id=task_id, persona_id=persona,
        dimension_scores=DimensionScores(
            completion=dim_completion, robustness=dim_robustness, safety=dim_safety),
        task_score=score, passed=passed,
        rubric_scores=[
            RubricScore(rubric_id=rid, dimension=dim, method="m",
                        weight=0.1, triggered=triggered, score=s)
            for rid, dim, s, triggered in rubric_scores
        ],
    )


# ----------------------------- _classify -----------------------------

def test_classify_improve():
    assert _classify(0.1, 0.05) == "improve"


def test_classify_regress():
    assert _classify(-0.1, 0.05) == "regress"


def test_classify_flat_when_below_threshold():
    assert _classify(0.03, 0.05) == "flat"
    assert _classify(-0.04, 0.05) == "flat"


# ----------------------------- _arrow -----------------------------

def test_arrow_strong_up():
    assert "↑↑↑" in _arrow(0.30)


def test_arrow_weak_up():
    s = _arrow(0.08)
    assert "↑" in s and "↑↑↑" not in s


def test_arrow_flat_below_threshold():
    s = _arrow(0.02)
    assert "↑" not in s and "↓" not in s


def test_arrow_down():
    assert "↓" in _arrow(-0.1)


def test_arrow_none():
    assert _arrow(None) == "(无)"


# ------------------------- compute_regression -------------------------

def test_compute_basic_improvement():
    old = [
        _result("t", "p1", 0.3, False,
                [("flow.x", "completion", 0.2, True),
                 ("safety.y", "safety", 1.0, True)],
                dim_completion=0.3),
        _result("t", "p1", 0.4, False,
                [("flow.x", "completion", 0.3, True),
                 ("safety.y", "safety", 1.0, True)],
                dim_completion=0.4),
    ]
    new = [
        _result("t", "p1", 0.8, True,
                [("flow.x", "completion", 0.8, True),
                 ("safety.y", "safety", 1.0, True)],
                dim_completion=0.8),
        _result("t", "p1", 0.85, True,
                [("flow.x", "completion", 0.9, True),
                 ("safety.y", "safety", 1.0, True)],
                dim_completion=0.85),
    ]
    rep = compute_regression(old, new, task_id="t",
                              old_label="v1", new_label="v2")

    assert rep.task_id == "t"
    assert rep.old_total == 2 and rep.new_total == 2
    # 平均分应改善
    assert rep.new_score_avg > rep.old_score_avg

    # flow.x 显著改进
    flow_x = next(r for r in rep.by_rubric if r.rubric_id == "flow.x")
    assert flow_x.significance == "improve"
    assert flow_x.delta > 0.4

    # safety.y 持平
    safety_y = next(r for r in rep.by_rubric if r.rubric_id == "safety.y")
    assert safety_y.significance == "flat"

    assert rep.n_improvements == 1
    assert rep.n_regressions == 0


def test_compute_detects_regression():
    old = [_result("t", "p", 0.8, True,
                    [("x.y", "completion", 0.9, True)])]
    new = [_result("t", "p", 0.4, False,
                    [("x.y", "completion", 0.3, True)])]
    rep = compute_regression(old, new, task_id="t")
    xy = next(r for r in rep.by_rubric if r.rubric_id == "x.y")
    assert xy.significance == "regress"
    assert rep.n_regressions == 1


def test_compute_added_rubric_marked():
    """new 里新加了 rubric,old 没有 → significance=added。"""
    old = [_result("t", "p", 0.5, False,
                    [("x.y", "completion", 0.5, True)])]
    new = [_result("t", "p", 0.5, False,
                    [("x.y", "completion", 0.5, True),
                     ("z.new", "robustness", 0.7, True)])]
    rep = compute_regression(old, new, task_id="t")
    z = next(r for r in rep.by_rubric if r.rubric_id == "z.new")
    assert z.significance == "added"
    assert z.old_avg is None
    assert z.new_avg == 0.7


def test_compute_removed_rubric_marked():
    old = [_result("t", "p", 0.5, False,
                    [("x.y", "completion", 0.5, True),
                     ("old.r", "robustness", 0.7, True)])]
    new = [_result("t", "p", 0.5, False,
                    [("x.y", "completion", 0.5, True)])]
    rep = compute_regression(old, new, task_id="t")
    rem = next(r for r in rep.by_rubric if r.rubric_id == "old.r")
    assert rem.significance == "removed"
    assert rem.new_avg is None


def test_compute_filters_by_task():
    """compute_regression 只关心传入的 task_id,其他混入的 result 应过滤掉。"""
    old = [_result("other", "p", 0.9, True, [])]
    new = [_result("other", "p", 0.9, True, [])]
    import pytest
    with pytest.raises(ValueError, match="完整评分"):
        compute_regression(old, new, task_id="my_task")


def test_compute_persona_diff():
    old = [
        _result("t", "p1", 0.4, False, []),
        _result("t", "p1", 0.5, False, []),
        _result("t", "p2", 0.9, True, []),
    ]
    new = [
        _result("t", "p1", 0.8, True, []),
        _result("t", "p1", 0.85, True, []),
        _result("t", "p2", 0.4, False, []),
    ]
    rep = compute_regression(old, new, task_id="t")
    p1 = next(p for p in rep.by_persona if p.persona_id == "p1")
    p2 = next(p for p in rep.by_persona if p.persona_id == "p2")
    # p1 从 0/2 通过 → 2/2 通过
    assert p1.old_pass_rate == 0.0
    assert p1.new_pass_rate == 1.0
    assert p1.delta_pass_rate > 0
    # p2 退化
    assert p2.delta_pass_rate < 0


# ------------------------- 终端格式 -------------------------

def test_terminal_output_includes_overview_and_arrows():
    old = [_result("t", "p", 0.3, False,
                    [("flow.x", "completion", 0.2, True)],
                    dim_completion=0.2)]
    new = [_result("t", "p", 0.8, True,
                    [("flow.x", "completion", 0.9, True)],
                    dim_completion=0.9)]
    rep = compute_regression(old, new, task_id="t", old_label="v1", new_label="v2")
    text = format_regression_terminal(rep)

    assert "回归对比" in text
    assert "v1" in text and "v2" in text
    assert "总览" in text
    assert "按 Rubric" in text
    assert "flow.x" in text
    assert "↑" in text                    # 应有上升箭头


# ------------------------- JSON save -------------------------

def test_save_and_reload(tmp_path: Path):
    old = [_result("t", "p", 0.3, False,
                    [("flow.x", "completion", 0.2, True)])]
    new = [_result("t", "p", 0.8, True,
                    [("flow.x", "completion", 0.9, True)])]
    rep = compute_regression(old, new, task_id="t")
    p = tmp_path / "reg.json"
    save_regression(rep, p)

    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["task_id"] == "t"
    assert data["n_improvements"] >= 1
    # by_rubric 是 list of dict
    assert any(r["rubric_id"] == "flow.x" for r in data["by_rubric"])
