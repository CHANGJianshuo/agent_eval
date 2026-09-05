"""关键词命中 matcher。"""
from __future__ import annotations

from . import MatcherResult


def check_keywords(text: str, keywords: list[str], mode: str = "any",
                   **_) -> MatcherResult:
    """检查 text 是否命中 keywords。

    mode=any : 命中任一即 1.0,否则 0.0
    mode=all : 按命中比例打分(全中 1.0)
    """
    if not keywords:
        return MatcherResult(score=1.0, detail="无关键词约束")

    hits = [k for k in keywords if k in text]
    if mode == "all":
        score = len(hits) / len(keywords)
    else:
        score = 1.0 if hits else 0.0

    detail = f"命中 {hits}" if hits else f"未命中任何关键词 {keywords}"
    return MatcherResult(score=round(score, 4), detail=detail)
