"""占位符残留检测 —— SUT 输出里若残留 ${...} 或字面 X/Y/Z/W,说明没代入真值。"""
from __future__ import annotations

import re

from ...models.trace import TraceMessage, Violation
from . import MatcherResult

_DEFAULT_PATTERNS = [
    r"\$\{[^}]*\}",                        # ${rider_name}
    r"(?<![A-Za-z])[XYZW](?=\s*[单天点])",  # 字面 X单 / Y天 / Z点
]


def check_placeholder(messages: list[TraceMessage],
                      patterns: list[str] | None = None, **_) -> MatcherResult:
    """扫描 assistant 输出。有任何残留 → score 0,否则 1。"""
    regexes = [re.compile(p) for p in (patterns or _DEFAULT_PATTERNS)]
    violations: list[Violation] = []

    for m in (x for x in messages if x.role == "assistant"):
        for rx in regexes:
            hit = rx.search(m.text)
            if hit:
                violations.append(Violation(
                    turn=m.turn,
                    detail=f"残留占位符 '{hit.group()}'",
                    evidence=m.text,
                ))
                break

    score = 0.0 if violations else 1.0
    return MatcherResult(score=score, violations=violations,
                         detail="有占位符残留" if violations else "无残留")
