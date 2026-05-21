"""Rubric 人审 gate 单测 —— 纯逻辑(safety 强制审 / 决策应用)。"""
from __future__ import annotations

from claw_eval.models.rubric import Rubric, TriggerSpec
from claw_eval.rubric.reviewer import (
    ReviewState,
    apply_decisions,
    format_rubric_summary,
    gate_blocked,
    interactive_review,
    needs_required_review,
)


def _r(rid: str, dim: str = "completion", is_safety: bool = False,
       method: str = "keyword") -> Rubric:
    return Rubric(id=rid, dimension=dim, method=method,
                  weight=0.1, check="x", is_safety=is_safety)


# ----------------------- needs_required_review -----------------------

def test_safety_dim_requires_review():
    assert needs_required_review(_r("safety.x", dim="safety", is_safety=True))


def test_is_safety_flag_requires_review():
    """即使 dimension 不是 safety,带 is_safety=true 也强制审。"""
    assert needs_required_review(_r("flow.x", is_safety=True))


def test_normal_rubric_not_required():
    assert not needs_required_review(_r("flow.x"))


# --------------------------- gate_blocked ---------------------------

def test_gate_blocks_when_safety_skipped():
    s = ReviewState(drafts=[_r("safety.x", dim="safety", is_safety=True)])
    # 未表态 = skip
    assert gate_blocked(s) == ["safety.x"]


def test_gate_clear_when_safety_decided():
    s = ReviewState(
        drafts=[_r("safety.x", dim="safety", is_safety=True)],
        decisions={"safety.x": "accept"})
    assert gate_blocked(s) == []


def test_gate_ignores_normal_skip():
    s = ReviewState(
        drafts=[_r("flow.x"), _r("safety.x", dim="safety", is_safety=True)],
        decisions={"safety.x": "reject"})
    # flow.x 未表态 = skip,但不阻塞(只是会被剔除)
    assert gate_blocked(s) == []


# --------------------------- apply_decisions ---------------------------

def test_apply_accept_keeps_and_marks_reviewed():
    s = ReviewState(drafts=[_r("flow.x")], decisions={"flow.x": "accept"})
    out = apply_decisions(s)
    assert len(out) == 1
    assert out[0].id == "flow.x"
    assert out[0].reviewed is True


def test_apply_reject_drops():
    s = ReviewState(drafts=[_r("flow.x")], decisions={"flow.x": "reject"})
    assert apply_decisions(s) == []


def test_apply_skip_drops():
    s = ReviewState(drafts=[_r("flow.x")])    # 默认 skip
    assert apply_decisions(s) == []


def test_apply_edit_uses_edits_dict():
    original = _r("flow.x")
    edited = original.model_copy(update={"weight": 0.5})
    s = ReviewState(
        drafts=[original],
        decisions={"flow.x": "edit"},
        edits={"flow.x": edited},
    )
    out = apply_decisions(s)
    assert out[0].weight == 0.5
    assert out[0].reviewed is True


# --------------------------- format_summary ---------------------------

def test_format_includes_id_and_check():
    r = _r("opening.greeting")
    text = format_rubric_summary(r)
    assert "opening.greeting" in text
    assert "method=keyword" in text


def test_format_marks_safety():
    r = _r("safety.x", dim="safety", is_safety=True)
    text = format_rubric_summary(r)
    assert "is_safety" in text or "★" in text


def test_format_includes_trigger():
    r = Rubric(id="faq.x", dimension="completion", method="llm_judge",
               weight=0.1, check="x",
               trigger=TriggerSpec(type="user_keyword", keywords=["退出"]))
    text = format_rubric_summary(r)
    assert "trigger" in text
    assert "退出" in text


# --------------------------- interactive_review ---------------------------

def test_interactive_review_with_scripted_inputs():
    """注入 prompt_fn / echo,验证决策被正确记录。"""
    drafts = [
        _r("flow.x"),
        _r("safety.y", dim="safety", is_safety=True),
        _r("constraint.z"),
    ]
    answers = iter(["a", "a", "r"])
    echoes: list[str] = []

    state = interactive_review(
        drafts,
        prompt_fn=lambda _: next(answers),
        echo=lambda s: echoes.append(s),
    )
    assert state.decisions == {"flow.x": "accept",
                                "safety.y": "accept",
                                "constraint.z": "reject"}


def test_interactive_review_blocks_skip_on_safety():
    """safety 类按 's' 应被拒,要求重新输入。"""
    drafts = [_r("safety.x", dim="safety", is_safety=True)]
    answers = iter(["s", "a"])             # 先 s 被拒,再 a 接受
    state = interactive_review(
        drafts,
        prompt_fn=lambda _: next(answers),
        echo=lambda _: None,
    )
    assert state.decisions["safety.x"] == "accept"
