"""Meta-Eval 单测 —— 抽样分层性 + 一致率计算 + 标注存取。"""
from __future__ import annotations

import json

import pytest

from claw_eval.meta_eval import (
    AnnotationItem,
    append_annotation,
    collect_judge_scores,
    compute_calibration,
    load_annotations,
    load_samples,
    save_samples,
    stratified_sample,
)


def _item(item_id: str, rubric: str, score: float) -> AnnotationItem:
    return AnnotationItem(
        item_id=item_id, task_id="t", run_id="r", trace_path="",
        persona_id="p", script_id="s", rubric_id=rubric,
        dimension="completion", judge_score=score,
        judge_reasoning="", evidence_turn=None)


# ------------------------- 抽样 -------------------------

def test_stratified_sample_covers_all_rubrics():
    items = (
        [_item(f"a{i}", "rub.a", i / 10) for i in range(10)]
        + [_item(f"b{i}", "rub.b", i / 10) for i in range(10)]
        + [_item(f"c{i}", "rub.c", i / 10) for i in range(10)]
    )
    out = stratified_sample(items, n=6, seed=1)
    assert len(out) == 6
    # 3 个 rubric 各 2 条(round-robin)
    by_rubric = {}
    for it in out:
        by_rubric.setdefault(it.rubric_id, []).append(it)
    assert set(by_rubric) == {"rub.a", "rub.b", "rub.c"}
    assert all(len(v) == 2 for v in by_rubric.values())


def test_stratified_sample_mixes_high_low_scores():
    items = [_item(f"x{i}", "rub.x", i / 20) for i in range(20)]
    out = stratified_sample(items, n=4, seed=1)
    scores = [it.judge_score for it in out]
    # 两端交替:必须同时有低分(<0.3)和高分(>0.7)
    assert min(scores) < 0.3
    assert max(scores) > 0.7


def test_stratified_sample_handles_n_larger_than_pool():
    items = [_item(f"x{i}", "rub.x", 0.5) for i in range(3)]
    out = stratified_sample(items, n=10, seed=1)
    assert len(out) == 3


# ------------------------- 收集 -------------------------

def test_collect_judge_scores_filters_method_and_trigger(tmp_path):
    run = tmp_path / "run1"
    run.mkdir()
    result = {
        "task_id": "demo",
        "persona_id": "p1",
        "script_id": "happy",
        "dimension_scores": {"completion": 1, "robustness": 1, "safety": 1},
        "task_score": 0.9,
        "passed": True,
        "rubric_scores": [
            {"rubric_id": "flow.a", "dimension": "completion",
             "method": "llm_judge", "weight": 0.2, "triggered": True,
             "score": 0.8, "reasoning": "ok", "evidence_turn": 3},
            {"rubric_id": "len.b", "dimension": "robustness",
             "method": "length", "weight": 0.1, "triggered": True,
             "score": 1.0, "reasoning": "", "evidence_turn": None},
            {"rubric_id": "faq.c", "dimension": "completion",
             "method": "llm_judge", "weight": 0.1, "triggered": False,
             "score": 0.0, "reasoning": "", "evidence_turn": None},
        ],
    }
    (run / "demo_p1_t1.result.json").write_text(
        json.dumps(result), encoding="utf-8")

    items = collect_judge_scores(tmp_path)
    # 只收 llm_judge 且 triggered 的 → 1 条
    assert len(items) == 1
    assert items[0].rubric_id == "flow.a"
    assert items[0].script_id == "happy"
    assert items[0].judge_score == 0.8


# ------------------------- 一致率 -------------------------

def _sample_dict(item_id: str, rubric: str, judge: float) -> dict:
    return _item(item_id, rubric, judge).to_dict()


def test_calibration_agree_means_match():
    samples = [_sample_dict("i1", "rub.a", 0.8)]
    anns = [{"item_id": "i1", "agree": True}]
    rep = compute_calibration(samples, anns)
    assert rep.n_annotated == 1
    assert rep.agreement_rate == 1.0
    assert rep.mean_bias == 0.0
    assert rep.disagreements == []


def test_calibration_disagree_within_tolerance_still_agrees():
    # 人工 0.7 vs judge 0.8,差 0.1 ≤ 0.2 → 算一致
    samples = [_sample_dict("i1", "rub.a", 0.8)]
    anns = [{"item_id": "i1", "agree": False, "human_score": 0.7}]
    rep = compute_calibration(samples, anns)
    assert rep.agreement_rate == 1.0


def test_calibration_large_gap_counts_disagreement_and_bias():
    samples = [
        _sample_dict("i1", "rub.a", 1.0),
        _sample_dict("i2", "rub.a", 0.9),
        _sample_dict("i3", "rub.b", 0.5),
    ]
    anns = [
        {"item_id": "i1", "agree": False, "human_score": 0.0,
         "comment": "judge 被套话骗了"},
        {"item_id": "i2", "agree": True},
        {"item_id": "i3", "agree": True},
    ]
    rep = compute_calibration(samples, anns)
    assert rep.n_annotated == 3
    assert rep.agreement_rate == round(2 / 3, 4)
    # bias = mean(1.0-0.0, 0, 0) > 0 → Judge 偏松
    assert rep.mean_bias == round(1.0 / 3, 4)
    assert len(rep.disagreements) == 1
    assert rep.disagreements[0]["rubric_id"] == "rub.a"
    # 按 rubric:rub.a 一致率 0.5,rub.b 1.0
    assert rep.by_rubric["rub.a"]["agreement_rate"] == 0.5
    assert rep.by_rubric["rub.b"]["agreement_rate"] == 1.0


def test_calibration_unannotated_not_counted():
    samples = [
        _sample_dict("i1", "rub.a", 0.8),
        _sample_dict("i2", "rub.a", 0.6),
    ]
    anns = [{"item_id": "i1", "agree": True}]
    rep = compute_calibration(samples, anns)
    assert rep.n_samples == 2
    assert rep.n_annotated == 1


# ------------------------- 存取 -------------------------

def test_save_load_samples_roundtrip(tmp_path):
    samples = [_item("i1", "rub.a", 0.8), _item("i2", "rub.b", 0.3)]
    save_samples(tmp_path, "demo", samples)
    loaded = load_samples(tmp_path, "demo")
    assert len(loaded) == 2
    assert loaded[0]["item_id"] == "i1"


def test_annotations_append_and_dedup_keeps_latest(tmp_path):
    append_annotation(tmp_path, "demo",
                      {"item_id": "i1", "agree": True})
    append_annotation(tmp_path, "demo",
                      {"item_id": "i1", "agree": False, "human_score": 0.2})
    anns = load_annotations(tmp_path, "demo")
    assert len(anns) == 1
    assert anns[0]["agree"] is False    # 同 item 重标,保留最后一条
