"""规则匹配器单测 —— 不依赖 API。"""
from __future__ import annotations

from claw_eval.graders.base import AbstractGrader
from claw_eval.graders.matchers.keyword import check_keywords
from claw_eval.graders.matchers.length import check_length
from claw_eval.graders.matchers.number_whitelist import check_number_whitelist
from claw_eval.graders.matchers.placeholder import check_placeholder
from claw_eval.models.rubric import Rubric
from claw_eval.models.task import TaskDefinition
from claw_eval.models.trace import TraceMessage


def _a(turn: int, text: str) -> TraceMessage:
    return TraceMessage(turn=turn, role="assistant", text=text)


# ----------------------------- length -----------------------------
def test_length_all_within_limit():
    msgs = [_a(2, "好的没问题"), _a(4, "路上注意安全")]
    res = check_length(msgs, max_chars=30, tolerance=0.1)
    assert res.score == 1.0
    assert res.violations == []


def test_length_one_violation_scores_half():
    msgs = [_a(2, "短回复"), _a(4, "字" * 40)]   # 第二轮 40 字,超 33 上限
    res = check_length(msgs, max_chars=30, tolerance=0.1)
    assert res.score == 0.5
    assert len(res.violations) == 1
    assert res.violations[0].turn == 4


def test_length_boundary_within_tolerance():
    # 33 字 = 30 * 1.1,正好在容差上限内
    res = check_length([_a(2, "字" * 33)], max_chars=30, tolerance=0.1)
    assert res.score == 1.0


# --------------------------- placeholder ---------------------------
def test_placeholder_clean():
    res = check_placeholder([_a(2, "你好张师傅,合同生效了")])
    assert res.score == 1.0


def test_placeholder_residue_dollar_brace():
    res = check_placeholder([_a(2, "你好${rider_name},合同生效了")])
    assert res.score == 0.0
    assert res.violations[0].turn == 2


def test_placeholder_residue_canonical_brace():
    res = check_placeholder([_a(2, "你好{name},合同生效了")])
    assert res.score == 0.0


def test_placeholder_accepts_legacy_singular_pattern_param():
    res = check_placeholder([_a(2, "值是<missing>")], pattern=r"<[^>]+>")
    assert res.score == 0.0


def test_placeholder_residue_literal_letter():
    res = check_placeholder([_a(2, "每天至少完成 X 单")])
    assert res.score == 0.0


# ----------------------------- keyword -----------------------------
def test_keyword_any_hit():
    res = check_keywords("你好我是站长", ["站长", "队长"], mode="any")
    assert res.score == 1.0


def test_keyword_all_partial():
    res = check_keywords("你好我是站长", ["站长", "飞毛腿"], mode="all")
    assert res.score == 0.5


def test_keyword_all_full():
    res = check_keywords("我是站长,你报了飞毛腿", ["站长", "飞毛腿"], mode="all")
    assert res.score == 1.0


# ------------------------ number_whitelist -------------------------
def test_number_whitelist_ok():
    res = check_number_whitelist([_a(2, "今天完成20单就行")], whitelist=["20", "10"])
    assert res.score == 1.0


def test_number_whitelist_hallucination():
    res = check_number_whitelist([_a(2, "今天必须跑满99单")], whitelist=["20", "10"])
    assert res.score == 0.0
    assert "99" in res.violations[0].detail


def test_number_whitelist_no_numbers():
    res = check_number_whitelist([_a(2, "好的注意安全")], whitelist=["20"])
    assert res.score == 1.0


def test_dispatch_number_whitelist_merges_task_and_config_values():
    task = TaskDefinition(task_id="t", prompt="p", variables={"count": 20})
    rubric = Rubric(
        id="safety.number",
        dimension="safety",
        method="number_whitelist",
        check="不编造数字",
        weight=1.0,
        is_safety=True,
        params={"whitelist": [30]},
    )

    score, *_ = AbstractGrader._dispatch_rubric(
        rubric,
        [_a(2, "每天20单，回复控制在30字")],
        task,
        "",
        None,
    )

    assert score == 1.0
