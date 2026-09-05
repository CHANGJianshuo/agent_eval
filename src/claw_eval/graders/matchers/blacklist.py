"""黑名单词检测 —— assistant 输出里出现禁用口头语就扣分。

典型用法:直播任务里禁用「好的 / 哈哈 / 嘿嘿 / 嘻嘻」等。
"""
from __future__ import annotations

from ...models.trace import TraceMessage, Violation
from . import MatcherResult


def check_blacklist(messages: list[TraceMessage],
                    banned_words: list[str] | None = None,
                    **_) -> MatcherResult:
    """assistant 任一轮命中黑名单词 → 该轮违规。score = 合规轮数 / 总轮数。"""
    banned = banned_words or []
    turns = [m for m in messages if m.role == "assistant"]
    if not turns or not banned:
        return MatcherResult(score=1.0, detail="无 assistant 轮或无黑名单")

    violations: list[Violation] = []
    for m in turns:
        for w in banned:
            if w in m.text:
                violations.append(Violation(
                    turn=m.turn,
                    detail=f"使用了禁用口头语 '{w}'",
                    evidence=m.text,
                ))
                break
    score = 1.0 - len(violations) / len(turns)
    return MatcherResult(
        score=round(score, 4),
        violations=violations,
        detail=f"{len(turns) - len(violations)}/{len(turns)} 轮无禁用词",
    )
