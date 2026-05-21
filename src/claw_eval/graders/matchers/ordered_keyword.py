"""顺序关键词匹配 —— 关键词必须按指定顺序依次出现在文本中。

用于检测「步进引导」类 rubric:[我的] → [服务商管理] → [直播平台] → [勾选保存]
必须按该顺序出现,否则视为漏步或乱序。
"""
from __future__ import annotations

from . import MatcherResult


def check_ordered_keyword(text: str, sequence: list[str], **_) -> MatcherResult:
    """sequence 中的关键词需按给定顺序在 text 中依次出现。

    score = 按序命中数 / len(sequence)。
    """
    if not sequence:
        return MatcherResult(score=1.0, detail="无顺序约束")

    pos = -1
    hit = 0
    for kw in sequence:
        idx = text.find(kw, pos + 1)
        if idx == -1:
            break
        pos = idx
        hit += 1

    score = hit / len(sequence)
    if hit == len(sequence):
        detail = f"按序命中全部 {hit}/{len(sequence)} 步"
    else:
        detail = (f"按序命中 {hit}/{len(sequence)} 步,"
                  f"卡在「{sequence[hit]}」未按序出现")
    return MatcherResult(score=round(score, 4), detail=detail)
