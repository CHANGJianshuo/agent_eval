"""评分器抽象基类 + 共享 helper + 通用 method 派发。

各任务的 grader.py 继承 AbstractGrader,自己写 grade() 编排;通过
`_dispatch_rubric` 复用所有标准方法(length / placeholder / keyword /
number_whitelist / ordered_keyword / pace_checker / blacklist / llm_judge)。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models.rubric import Rubric
from ..models.task import TaskDefinition
from ..models.trace import GradingResult, TraceMessage, Violation


# llm_judge 得分低于此值,记一条 violation
JUDGE_VIOLATION_THRESHOLD = 0.6


class AbstractGrader(ABC):
    """所有任务评分器的基类。"""

    @abstractmethod
    def grade(self, messages: list[TraceMessage], task: TaskDefinition,
              rubrics: list[Rubric], judge=None) -> GradingResult:
        """评分一条 trace,返回 GradingResult。"""
        ...

    # ------------------------------------------------------------------
    # 通用 method 派发(各任务 grader.py 可直接调用)
    # ------------------------------------------------------------------
    @classmethod
    def _dispatch_rubric(cls, rubric: Rubric, messages: list[TraceMessage],
                         task: TaskDefinition, conversation: str, judge,
                         ) -> tuple[float, str, int | None, list[Violation]]:
        """跑单条 rubric,返回 (score, reasoning, evidence_turn, violations)。"""
        from .matchers.blacklist import check_blacklist
        from .matchers.keyword import check_keywords
        from .matchers.length import check_length
        from .matchers.number_whitelist import check_number_whitelist
        from .matchers.ordered_keyword import check_ordered_keyword
        from .matchers.pace_checker import check_pace
        from .matchers.placeholder import check_placeholder

        method = rubric.method
        params = dict(rubric.params)

        if method == "length":
            res = check_length(messages, **params)
            return res.score, res.detail, None, res.violations

        if method == "placeholder":
            res = check_placeholder(messages, **params)
            return res.score, res.detail, None, res.violations

        if method == "keyword":
            text = cls._scope_text(messages, params.get("scope", "all_assistant"))
            res = check_keywords(text, params.get("keywords", []),
                                 params.get("mode", "any"))
            vios: list[Violation] = []
            if res.score < 1.0:
                vios.append(Violation(
                    turn=cls._first_assistant_turn(messages),
                    detail=res.detail or "关键词未命中",
                ))
            return res.score, res.detail, None, vios

        if method == "number_whitelist":
            whitelist = list(task.number_whitelist())
            whitelist += [str(x) for x in params.get("extra_whitelist", [])]
            res = check_number_whitelist(messages, whitelist=whitelist)
            return res.score, res.detail, None, res.violations

        if method == "ordered_keyword":
            text = cls._scope_text(messages, params.get("scope", "all_assistant"))
            res = check_ordered_keyword(text, params.get("sequence", []))
            vios = []
            if res.score < 1.0:
                vios.append(Violation(detail=res.detail))
            return res.score, res.detail, None, vios

        if method == "pace_checker":
            res = check_pace(messages, **params)
            vios = []
            if res.score < 1.0:
                vios.append(Violation(detail=res.detail))
            return res.score, res.detail, None, vios

        if method == "blacklist":
            res = check_blacklist(messages, **params)
            return res.score, res.detail, None, res.violations

        if method == "llm_judge":
            if judge is None:
                return 0.0, "未提供 LLM Judge,跳过", None, []
            check = cls._fmt(rubric.check, task.variables)
            trigger_desc = rubric.trigger.desc if rubric.trigger else ""
            jr = judge.evaluate(check, conversation, trigger=trigger_desc)
            vios = []
            if jr.score < JUDGE_VIOLATION_THRESHOLD:
                vios.append(Violation(
                    turn=jr.evidence_turn,
                    detail=jr.reasoning,
                    evidence=cls.turn_text(messages, jr.evidence_turn),
                ))
            return jr.score, jr.reasoning, jr.evidence_turn, vios

        return 0.0, f"未知 method:{method}", None, []

    # ------------------------------------------------------------------
    # 共享 helper
    # ------------------------------------------------------------------
    @staticmethod
    def _fmt(text: str, variables: dict) -> str:
        """把 rubric.check 里的 {变量} 代入,保持评分标准与 task 变量同源。"""
        try:
            return text.format(**variables)
        except (KeyError, IndexError, ValueError):
            return text

    @staticmethod
    def _scope_text(messages: list[TraceMessage], scope: str) -> str:
        if scope == "first_assistant":
            for m in messages:
                if m.role == "assistant":
                    return m.text
            return ""
        return "\n".join(m.text for m in messages if m.role == "assistant")

    @staticmethod
    def _first_assistant_turn(messages: list[TraceMessage]) -> int | None:
        for m in messages:
            if m.role == "assistant":
                return m.turn
        return None

    @staticmethod
    def assistant_turns(messages: list[TraceMessage]) -> list[TraceMessage]:
        return [m for m in messages if m.role == "assistant"]

    @staticmethod
    def user_turns(messages: list[TraceMessage]) -> list[TraceMessage]:
        return [m for m in messages if m.role == "user"]

    @staticmethod
    def first_assistant_text(messages: list[TraceMessage]) -> str:
        for m in messages:
            if m.role == "assistant":
                return m.text
        return ""

    @staticmethod
    def all_assistant_text(messages: list[TraceMessage]) -> str:
        return "\n".join(m.text for m in messages if m.role == "assistant")

    @staticmethod
    def turn_text(messages: list[TraceMessage], turn: int | None) -> str:
        if turn is None:
            return ""
        for m in messages:
            if m.turn == turn:
                return m.text
        return ""

    @staticmethod
    def format_conversation(messages: list[TraceMessage]) -> str:
        """拼成带轮号的通话记录,供 LLM Judge 引用 evidence_turn_id。"""
        lines = []
        for m in messages:
            who = "站长" if m.role == "assistant" else "骑手"
            lines.append(f"[第{m.turn}轮 {who}]: {m.text}")
        return "\n".join(lines)
