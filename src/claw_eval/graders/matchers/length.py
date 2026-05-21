"""字数检测 matcher —— 中文按字符 len() 计。"""
from __future__ import annotations

from ...models.trace import TraceMessage, Violation
from . import MatcherResult


def check_length(messages: list[TraceMessage], max_chars: int = 30,
                 tolerance: float = 0.1, **_) -> MatcherResult:
    """逐 assistant 轮检查字数。score = 合规轮数 / 总轮数。"""
    limit = max_chars * (1 + tolerance)
    turns = [m for m in messages if m.role == "assistant"]
    if not turns:
        return MatcherResult(score=1.0, detail="无 assistant 轮")

    violations: list[Violation] = []
    for m in turns:
        n = len(m.text.strip())
        if n > limit:
            violations.append(Violation(
                turn=m.turn,
                detail=f"{n} 字,超过 {max_chars} 字上限(含容差上限 {limit:.0f})",
                evidence=m.text,
            ))
    score = 1.0 - len(violations) / len(turns)
    return MatcherResult(
        score=round(score, 4),
        violations=violations,
        detail=f"{len(turns) - len(violations)}/{len(turns)} 轮字数合规",
    )
