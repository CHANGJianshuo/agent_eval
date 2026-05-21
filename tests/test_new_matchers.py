"""新匹配器单测:ordered_keyword / pace_checker / blacklist —— 不依赖 API。"""
from __future__ import annotations

from claw_eval.graders.matchers.blacklist import check_blacklist
from claw_eval.graders.matchers.ordered_keyword import check_ordered_keyword
from claw_eval.graders.matchers.pace_checker import check_pace
from claw_eval.models.trace import TraceMessage


def _a(turn: int, text: str) -> TraceMessage:
    return TraceMessage(turn=turn, role="assistant", text=text)


def _u(turn: int, text: str) -> TraceMessage:
    return TraceMessage(turn=turn, role="user", text=text)


# --------------------------- ordered_keyword ---------------------------

def test_ordered_keyword_full_in_order():
    text = "请先点【我的】,然后进【服务商管理】,选【直播平台】,最后勾选保存"
    res = check_ordered_keyword(text, ["我的", "服务商管理", "直播平台", "勾选"])
    assert res.score == 1.0


def test_ordered_keyword_partial():
    text = "点【我的】,然后进【服务商管理】,后面忘了讲"
    res = check_ordered_keyword(text, ["我的", "服务商管理", "直播平台", "勾选"])
    assert res.score == 0.5
    assert "直播平台" in res.detail


def test_ordered_keyword_out_of_order_counted_as_missing():
    # 「我的」最先按序命中(在尾部),其后再找「服务商管理」时已找不到 → 停在第 2 步
    text = "点【直播平台】,然后进【服务商管理】,最后【我的】里再勾选"
    res = check_ordered_keyword(text, ["我的", "服务商管理", "直播平台", "勾选"])
    assert res.score == 0.25
    assert "服务商管理" in res.detail


def test_ordered_keyword_empty_sequence():
    res = check_ordered_keyword("任意文本", [])
    assert res.score == 1.0


# ----------------------------- pace_checker -----------------------------

def test_pace_no_trigger_means_skip():
    msgs = [_u(1, "你好"), _a(2, "您好")]
    res = check_pace(msgs, min_assistant_turns=4,
                     after_user_keyword=["没看到"])
    assert res.score == 1.0
    assert "跳过" in res.detail


def test_pace_after_trigger_enough_turns():
    msgs = [
        _u(1, "您好"),
        _a(2, "您好我是客服"),
        _u(3, "我没看到这个选项"),     # 触发
        _a(4, "您先点我的"),
        _a(5, "然后进服务商管理"),
        _a(6, "再点直播平台"),
        _a(7, "最后勾选保存"),
    ]
    res = check_pace(msgs, min_assistant_turns=4,
                     after_user_keyword=["没看到"])
    assert res.score == 1.0


def test_pace_after_trigger_one_dump_fails():
    msgs = [
        _u(1, "您好"),
        _u(3, "我没看到这个选项"),
        _a(4, "点我的 → 服务商管理 → 直播平台 → 勾选保存,一次到位"),  # 1 轮塞 4 步
    ]
    res = check_pace(msgs, min_assistant_turns=4,
                     after_user_keyword=["没看到"])
    assert res.score == 0.25       # 1/4


# ------------------------------- blacklist -------------------------------

def test_blacklist_clean():
    res = check_blacklist([_a(2, "您好,我跟您介绍下")],
                          banned_words=["好的", "哈哈", "嘿嘿"])
    assert res.score == 1.0


def test_blacklist_one_violation_among_two():
    msgs = [_a(2, "您好,我跟您介绍下"), _a(4, "好的,那就这样")]
    res = check_blacklist(msgs, banned_words=["好的", "哈哈"])
    assert res.score == 0.5
    assert res.violations[0].turn == 4
    assert "好的" in res.violations[0].detail


def test_blacklist_empty_banned():
    res = check_blacklist([_a(2, "随便说什么")], banned_words=[])
    assert res.score == 1.0
