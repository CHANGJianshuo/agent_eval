"""数字白名单 —— assistant 输出里出现的数字必须都在白名单内,否则视为编造。"""
from __future__ import annotations

import re

from ...models.trace import TraceMessage, Violation
from . import MatcherResult

_NUM = re.compile(r"\d+(?:\.\d+)?")


def check_number_whitelist(messages: list[TraceMessage],
                           whitelist: list[str] | None = None,
                           **_) -> MatcherResult:
    """出现任何白名单外的数字 → score 0(防数字幻觉)。"""
    allowed = {str(w) for w in (whitelist or [])}
    violations: list[Violation] = []

    for m in (x for x in messages if x.role == "assistant"):
        for num in _NUM.findall(m.text):
            if num not in allowed:
                violations.append(Violation(
                    turn=m.turn,
                    detail=f"出现白名单外数字 '{num}'(允许:{sorted(allowed)})",
                    evidence=m.text,
                ))

    score = 0.0 if violations else 1.0
    return MatcherResult(score=score, violations=violations,
                         detail="有编造数字嫌疑" if violations else "数字均在白名单内")
