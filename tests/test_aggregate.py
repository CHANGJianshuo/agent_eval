"""聚合层单测:跨 case 的 by_persona / by_rubric / heatmap 计算。"""
from __future__ import annotations

from claw_eval.report.aggregate import aggregate
from claw_eval.models.trace import (
    DimensionScores,
    GradingResult,
    RubricScore,
)


def _rs(rid: str, dim: str, score: float, triggered: bool = True) -> RubricScore:
    return RubricScore(rubric_id=rid, dimension=dim, method="m",
                       weight=1.0, score=score, triggered=triggered)


def _result(persona: str, task_score: float, passed: bool,
            completion: float, robustness: float, safety: float,
            rubrics: list[RubricScore]) -> GradingResult:
    return GradingResult(
        task_id="t",
        persona_id=persona,
        dimension_scores=DimensionScores(
            completion=completion, robustness=robustness, safety=safety),
        task_score=task_score,
        passed=passed,
        rubric_scores=rubrics,
    )


def test_aggregate_empty():
    s = aggregate([])
    assert s.total_runs == 0
    assert s.pass_count == 0
    assert s.pass_rate == 0.0


def test_aggregate_totals_and_avgs():
    results = [
        _result("p1", 0.80, True, 1.0, 0.5, 1.0, []),
        _result("p2", 0.60, False, 0.8, 0.0, 1.0, []),
    ]
    s = aggregate(results)
    assert s.total_runs == 2
    assert s.pass_count == 1
    assert s.pass_rate == 0.5
    assert s.avg_completion == 0.9     # (1.0 + 0.8) / 2
    assert s.avg_robustness == 0.25
    assert s.avg_safety == 1.0


def test_aggregate_by_persona_rolls_up_multiple_trials():
    # p1 跑两次:1 过 1 不过 → pass_rate 0.5
    results = [
        _result("p1", 0.80, True, 1.0, 1.0, 1.0, []),
        _result("p1", 0.60, False, 0.8, 0.0, 1.0, []),
        _result("p2", 0.90, True, 1.0, 0.5, 1.0, []),
    ]
    s = aggregate(results)
    assert s.by_persona["p1"]["n"] == 2
    assert s.by_persona["p1"]["pass_n"] == 1
    assert s.by_persona["p1"]["pass_rate"] == 0.5
    assert s.by_persona["p1"]["completion"] == 0.9     # (1.0+0.8)/2
    assert s.by_persona["p2"]["n"] == 1
    assert s.by_persona["p2"]["pass_rate"] == 1.0


def test_aggregate_by_rubric_skips_untriggered():
    rs_triggered = _rs("flow.x", "completion", 0.8, triggered=True)
    rs_not = _rs("faq.y", "completion", 0.0, triggered=False)
    rs_safe = _rs("safety.z", "safety", 1.0, triggered=True)

    results = [_result("p1", 0.8, True, 0.8, 1.0, 1.0,
                       [rs_triggered, rs_not, rs_safe])]
    s = aggregate(results)

    assert "flow.x" in s.by_rubric
    assert s.by_rubric["flow.x"]["n"] == 1
    assert s.by_rubric["flow.x"]["avg_score"] == 0.8
    # 未触发的不入 by_rubric
    assert "faq.y" not in s.by_rubric
    assert "safety.z" in s.by_rubric

    # rubric_order 记录全部出现过的(含未触发的)
    assert s.rubric_order == ["flow.x", "faq.y", "safety.z"]
    assert s.rubric_dim["safety.z"] == "safety"


def test_aggregate_heatmap_structure():
    results = [
        _result("p1", 1.0, True, 1.0, 1.0, 1.0, [
            _rs("a", "completion", 0.9),
            _rs("b", "robustness", 0.5),
        ]),
        _result("p2", 0.8, True, 0.9, 0.8, 1.0, [
            _rs("a", "completion", 0.7),
        ]),
    ]
    s = aggregate(results)
    # p1 row 有 a 和 b
    assert s.heatmap["p1"]["a"] == 0.9
    assert s.heatmap["p1"]["b"] == 0.5
    # p2 row 只有 a(未提及 b → 不在 row 里)
    assert s.heatmap["p2"]["a"] == 0.7
    assert "b" not in s.heatmap["p2"]
