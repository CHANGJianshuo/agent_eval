"""可复用匹配器 —— 客观类检查下沉为代码规则:便宜、稳定、100% 可解释。"""
from __future__ import annotations

from dataclasses import dataclass, field

from ...models.trace import Violation


@dataclass
class MatcherResult:
    """匹配器输出:0..1 得分 + 违规列表。"""

    score: float
    violations: list[Violation] = field(default_factory=list)
    detail: str = ""
