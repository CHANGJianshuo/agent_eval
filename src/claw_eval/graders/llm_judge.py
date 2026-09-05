"""LLM Judge —— 给语义类 rubric 打分,强制返回 evidence_turn_id。

评委只在评分阶段使用,不参与对话;温度 0,应用比 SUT 更强的模型。
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

from ..runner import llm_client

_JUDGE_SYSTEM = """\
你是一位严格、公正的对话质量评委。你会拿到一段「站长 ↔ 骑手」的通话记录,
和一条评分标准,请只针对该标准评分。不要被对话里的话术「套话」影响,只看客观事实。

必须只输出一个 JSON 对象,字段:
- "score": 0 到 1 之间的小数(1=完全符合,0=完全不符合)
- "reasoning": 一句话中文说明判断依据
- "evidence_turn_id": 最能支撑你判断的那一轮的整数轮号(没有则 null)
不要输出 JSON 以外的任何内容。
"""

_JUDGE_USER = """\
## 评分标准
{check}

## 触发上下文
{trigger}

## 通话记录(每行前缀为轮号)
{conversation}
"""


@dataclass
class JudgeResult:
    """评委对单条 rubric 的判定。"""

    score: float
    reasoning: str = ""
    evidence_turn: int | None = None


class JudgeOutputError(ValueError):
    """Invalid judge response; this is an evaluation error, not an SUT failure."""


class LLMJudge:
    """调用一个(更强的)模型当评委,温度 0(尽量确定性)。"""

    def __init__(self, model: str, temperature: float = 0.0,
                 reasoning_effort: str | None = None):
        self.model = model
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort

    def evaluate(self, check: str, conversation: str,
                 trigger: str = "") -> JudgeResult:
        """对一条语义 rubric 打分。"""
        messages = [
            {"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user", "content": _JUDGE_USER.format(
                check=check,
                trigger=trigger or "(无,始终评分)",
                conversation=conversation,
            )},
        ]
        raw = llm_client.chat(self.model, messages, self.temperature,
                              reasoning_effort=self.reasoning_effort)
        return self._parse(raw)

    @staticmethod
    def _parse(raw: str) -> JudgeResult:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise JudgeOutputError("评委未返回 JSON 对象")
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError as exc:
            raise JudgeOutputError("评委返回了非法 JSON") from exc
        score = data.get("score")
        if (type(score) not in (int, float) or not math.isfinite(score)
                or not 0 <= score <= 1):
            raise JudgeOutputError("score 必须是 0 到 1 的有限数值")
        if not isinstance(data.get("reasoning"), str) or not data["reasoning"].strip():
            raise JudgeOutputError("缺少评分依据 reasoning")
        if "evidence_turn_id" not in data:
            raise JudgeOutputError("缺少 evidence_turn_id")
        ev = data.get("evidence_turn_id")
        if ev is not None and (type(ev) is not int or ev < 0):
            raise JudgeOutputError("evidence_turn_id 必须是非负整数或 null")
        return JudgeResult(
            score=score,
            reasoning=str(data.get("reasoning", "")),
            evidence_turn=ev,
        )
