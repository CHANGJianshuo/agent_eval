"""改进建议产出单测 —— 聚合(纯函数)+ LLM 解析(mock)。"""
from __future__ import annotations

from pathlib import Path

from claw_eval.models.rubric import Rubric
from claw_eval.models.task import TaskDefinition
from claw_eval.models.trace import (
    DimensionScores,
    GradingResult,
    RubricScore,
    Violation,
)
from claw_eval.report.aggregate import AggregateSummary
from claw_eval.report.recommend import (
    _parse_recommendation,
    build_recommendations,
    collect_violation_samples,
    find_weak_rubrics,
    load_recommendations,
    save_recommendations,
)


# --------------------------- find_weak_rubrics ---------------------------

def _summary(by_rubric: dict) -> AggregateSummary:
    s = AggregateSummary(total_runs=10, pass_count=3, pass_rate=0.3)
    s.by_rubric = by_rubric
    return s


def test_find_weak_orders_by_severity():
    s = _summary({
        "flow.a": {"n": 10, "avg_score": 0.3, "dimension": "completion"},   # 严重度 7
        "faq.b":  {"n": 5,  "avg_score": 0.6, "dimension": "completion"},   # 严重度 2
        "flow.c": {"n": 4,  "avg_score": 0.2, "dimension": "completion"},   # 严重度 3.2
    })
    weak = find_weak_rubrics(s, top_n=5)
    assert [w["rubric_id"] for w in weak] == ["flow.a", "flow.c", "faq.b"]


def test_find_weak_filters_strong():
    s = _summary({
        "good.x": {"n": 10, "avg_score": 0.9, "dimension": "completion"},   # 强,过滤
        "weak.y": {"n": 10, "avg_score": 0.5, "dimension": "completion"},
    })
    weak = find_weak_rubrics(s, top_n=5)
    assert [w["rubric_id"] for w in weak] == ["weak.y"]


def test_find_weak_filters_few_triggers():
    s = _summary({
        "rare.x": {"n": 1, "avg_score": 0.1, "dimension": "completion"},    # 触发太少,过滤
        "common.y": {"n": 5, "avg_score": 0.5, "dimension": "completion"},
    })
    weak = find_weak_rubrics(s, top_n=5)
    assert [w["rubric_id"] for w in weak] == ["common.y"]


def test_find_weak_top_n_caps():
    s = _summary({
        f"r.{i}": {"n": 5, "avg_score": 0.3, "dimension": "completion"}
        for i in range(10)
    })
    weak = find_weak_rubrics(s, top_n=3)
    assert len(weak) == 3


# --------------------------- collect_violation_samples ---------------------------

def _result(persona: str, rubric_scores: list[RubricScore],
            violations: list[Violation]) -> GradingResult:
    return GradingResult(
        task_id="t", persona_id=persona,
        dimension_scores=DimensionScores(),
        task_score=0.5, passed=False,
        rubric_scores=rubric_scores,
        violations=violations,
    )


def test_collect_picks_lowest_scoring_failures():
    results = [
        _result("p1", [
            RubricScore(rubric_id="x", dimension="completion", method="m",
                        weight=0.1, triggered=True, score=0.4,
                        reasoning="原因 A")
        ], [Violation(rubric_id="x", turn=3, detail="d", evidence="A 证据")]),
        _result("p2", [
            RubricScore(rubric_id="x", dimension="completion", method="m",
                        weight=0.1, triggered=True, score=0.2,
                        reasoning="原因 B")
        ], [Violation(rubric_id="x", turn=5, detail="d", evidence="B 证据")]),
        _result("p3", [
            RubricScore(rubric_id="x", dimension="completion", method="m",
                        weight=0.1, triggered=True, score=0.8,    # 没违规
                        reasoning="原因 OK")
        ], []),
    ]
    samples = collect_violation_samples(results, "x", top_k=5)
    # 只挑 score<0.6 的;0.2 排第一
    assert len(samples) == 2
    assert samples[0]["score"] == 0.2
    assert samples[1]["score"] == 0.4


def test_collect_skips_untriggered():
    results = [_result("p", [
        RubricScore(rubric_id="x", dimension="completion", method="m",
                    weight=0.1, triggered=False, score=0.0)
    ], [])]
    assert collect_violation_samples(results, "x") == []


# --------------------------- _parse_recommendation ---------------------------

def test_parse_full_yaml():
    text = """\
suggested_prompt_change: "把第 4 行改成 X"
rationale: "用户漏了 Y"
estimated_lift: 0.06
confidence: 0.85
"""
    rec = _parse_recommendation(text)
    assert rec["suggested_prompt_change"] == "把第 4 行改成 X"
    assert rec["estimated_lift"] == 0.06
    assert rec["confidence"] == 0.85


def test_parse_with_markdown_codeblock():
    text = '```yaml\nsuggested_prompt_change: "改 A"\nrationale: "B"\nestimated_lift: 0.05\nconfidence: 0.7\n```'
    rec = _parse_recommendation(text)
    assert rec["suggested_prompt_change"] == "改 A"


def test_parse_handles_invalid_gracefully():
    assert _parse_recommendation("not yaml at all") == {}


# --------------------------- build_recommendations 编排 ---------------------------

def test_build_recommendations_no_judge_skips_llm(monkeypatch):
    """no_judge 时只出聚合数据,不调 LLM。"""
    calls = []
    monkeypatch.setattr(
        "claw_eval.report.recommend.llm_client.chat",
        lambda *a, **k: calls.append(1) or "x")
    results = [_result("p", [
        RubricScore(rubric_id="flow.x", dimension="completion", method="m",
                    weight=0.1, triggered=True, score=0.3, reasoning="r")
        for _ in range(5)
    ], [Violation(rubric_id="flow.x", turn=1, detail="", evidence="e")])
    for _ in range(5)]
    # 多个 result 让 n >= 3 触发
    task = TaskDefinition(task_id="t", prompt="P")
    rubric = Rubric(id="flow.x", dimension="completion", method="m",
                     weight=0.1, check="c")
    recs = build_recommendations(task, results, [rubric],
                                  judge_model=None, top_n=5)
    assert len(recs) == 1
    assert recs[0]["rubric_id"] == "flow.x"
    assert "suggested_prompt_change" not in recs[0]
    assert calls == []                                           # 没调 LLM


def test_build_recommendations_with_judge_calls_llm(monkeypatch):
    captured = []
    fake_response = ("suggested_prompt_change: \"改这里\"\n"
                     "rationale: \"理由\"\n"
                     "estimated_lift: 0.05\n"
                     "confidence: 0.8")

    def fake_chat(model, messages, **kwargs):
        captured.append(model)
        return fake_response

    monkeypatch.setattr(
        "claw_eval.report.recommend.llm_client.chat", fake_chat)
    results = [_result("p", [
        RubricScore(rubric_id="flow.x", dimension="completion", method="m",
                    weight=0.1, triggered=True, score=0.3, reasoning="r")
    ], [Violation(rubric_id="flow.x", turn=1, detail="", evidence="e")])
    for _ in range(5)]
    task = TaskDefinition(task_id="t", prompt="P")
    rubric = Rubric(id="flow.x", dimension="completion", method="m",
                     weight=0.1, check="c")
    recs = build_recommendations(task, results, [rubric],
                                  judge_model="fake-model", top_n=5)
    assert len(recs) == 1
    assert recs[0]["suggested_prompt_change"] == "改这里"
    assert recs[0]["estimated_lift"] == 0.05
    assert captured == ["fake-model"]


# --------------------------- save / load ---------------------------

def test_save_and_load_recommendations(tmp_path: Path):
    recs = [{"rubric_id": "flow.x", "avg_score": 0.3, "severity": 1.5,
             "n_triggered": 5, "dimension": "completion",
             "violation_samples": []}]
    p = tmp_path / "recs.json"
    save_recommendations("t", recs, p)
    loaded = load_recommendations(p)
    assert loaded == recs


def test_load_missing_returns_empty(tmp_path: Path):
    assert load_recommendations(tmp_path / "nonexistent.json") == []
