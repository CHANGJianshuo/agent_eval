"""节奏检测 —— 步进式引导是否分轮发送(不能一次性灌输)。

实现:从用户触发关键词命中的那一轮起,统计后续的 assistant 轮数,
需达到 min_assistant_turns 才算分轮合规。
"""
from __future__ import annotations

from ...models.trace import TraceMessage
from . import MatcherResult


def check_pace(messages: list[TraceMessage], min_assistant_turns: int = 4,
               after_user_keyword: list[str] | str | None = None,
               **_) -> MatcherResult:
    """触发条件命中的用户轮之后,后续 assistant 轮数 ≥ min_assistant_turns。"""
    keywords: list[str]
    if after_user_keyword is None:
        keywords = []
    elif isinstance(after_user_keyword, str):
        keywords = [after_user_keyword]
    else:
        keywords = list(after_user_keyword)

    start_turn = 0
    if keywords:
        for m in messages:
            if m.role == "user" and any(k in m.text for k in keywords):
                start_turn = m.turn
                break
        if start_turn == 0:
            return MatcherResult(score=1.0, detail="触发用户关键词未命中,跳过节奏检查")

    n_assistant = sum(1 for m in messages
                      if m.role == "assistant" and m.turn > start_turn)
    if min_assistant_turns <= 0:
        score = 1.0
    else:
        score = min(n_assistant / min_assistant_turns, 1.0)
    return MatcherResult(
        score=round(score, 4),
        detail=(f"触发后 {n_assistant} 个站长轮(要求 ≥ {min_assistant_turns}),"
                f"{'合规' if n_assistant >= min_assistant_turns else '一次性灌输/分轮不足'}"),
    )
