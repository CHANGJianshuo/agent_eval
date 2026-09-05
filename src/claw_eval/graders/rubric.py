"""默认评分器：按任务的 rubrics.yaml 评分，共享触发与计分逻辑。"""
from __future__ import annotations

from . import scoring
from .base import AbstractGrader
from ..models.rubric import Rubric, TriggerSpec
from ..models.task import TaskDefinition
from ..models.trace import (
    GradingResult,
    RubricScore,
    TraceMessage,
    Violation,
)


class RubricGrader(AbstractGrader):
    """通过 rubric 配置执行规则匹配和语义评分。"""

    def grade(self, messages: list[TraceMessage], task: TaskDefinition,
              rubrics: list[Rubric], judge=None) -> GradingResult:
        conversation = self.format_conversation(messages)
        rubric_scores: list[RubricScore] = []
        violations: list[Violation] = []

        for r in rubrics:
            if r.dimension != "safety" and not r.is_safety and r.weight <= 0:
                rubric_scores.append(RubricScore(
                    rubric_id=r.id, dimension=r.dimension, method=r.method,
                    weight=r.weight, status="error", reasoning="非安全评分项必须有正权重",
                ))
                continue
            if not self._is_triggered(r.trigger, messages):
                rubric_scores.append(RubricScore(
                    rubric_id=r.id, dimension=r.dimension, method=r.method, is_safety=r.is_safety,
                    weight=r.weight, triggered=False, score=0.0, status="not_applicable",
                    reasoning="触发条件未满足,不计入评分",
                ))
                continue

            if r.method == "llm_judge" and judge is None:
                rubric_scores.append(RubricScore(
                    rubric_id=r.id, dimension=r.dimension, method=r.method, is_safety=r.is_safety,
                    weight=r.weight, triggered=True, score=0.0, status="skipped",
                    reasoning="未提供 LLM Judge,跳过语义项",
                ))
                continue

            try:
                from .validation import validate_rule_params
                validate_rule_params(r)
                score, reasoning, ev_turn, vios = self._dispatch_rubric(
                    r, messages, task, conversation, judge)
                if ev_turn is not None and ev_turn not in {m.turn for m in messages}:
                    raise ValueError(f"评委引用了不存在的轮次: {ev_turn}")
            except Exception as exc:
                rubric_scores.append(RubricScore(
                    rubric_id=r.id, dimension=r.dimension, method=r.method, is_safety=r.is_safety,
                    weight=r.weight, status="error", reasoning=f"评分失败: {type(exc).__name__}: {exc}",
                ))
                continue
            for v in vios:
                v.rubric_id = r.id
            violations.extend(vios)
            rubric_scores.append(RubricScore(
                rubric_id=r.id, dimension=r.dimension, method=r.method, is_safety=r.is_safety,
                weight=r.weight, triggered=True, score=score,
                reasoning=reasoning, evidence_turn=ev_turn,
            ))

        dim = scoring.compute_dimension_scores(rubric_scores)
        task_score = scoring.compute_task_score(dim)
        return GradingResult(
            task_id=task.task_id,
            dimension_scores=dim,
            task_score=task_score,
            passed=scoring.is_pass(task_score),
            rubric_scores=rubric_scores,
            violations=violations,
            status="complete" if rubrics else "incomplete",
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _is_triggered(trigger: TriggerSpec | None,
                      messages: list[TraceMessage]) -> bool:
        if trigger is None:
            return True
        if trigger.type == "probe":
            return any(m.probe_id == trigger.probe_id for m in messages)
        if trigger.type == "user_state":
            return any(m.role == "user" and m.state == trigger.state
                       for m in messages)
        if trigger.type == "user_keyword":
            user_text = "\n".join(m.text for m in messages if m.role == "user")
            return any(k in user_text for k in trigger.keywords)
        raise ValueError(f"不支持的 trigger type: {trigger.type}")
