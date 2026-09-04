"""rider_delivery_demo_0904 评分器(自动生成 - 模板填空 · 可手动调整)。

评分编排:读 rubrics → 检测 trigger → 调 _dispatch_rubric → 安全门
→ 加权组装 DimensionScores → 收集 violations(带 turn + evidence)。

如果某条 rubric 需要超出基础 dispatch 的逻辑,可以在这里添加自定义 helper。
"""
from __future__ import annotations

from claw_eval.graders import scoring
from claw_eval.graders.base import AbstractGrader
from claw_eval.models.rubric import Rubric, TriggerSpec
from claw_eval.models.task import TaskDefinition
from claw_eval.models.trace import (
    GradingResult,
    RubricScore,
    TraceMessage,
    Violation,
)


class RiderDeliveryDemo0904Grader(AbstractGrader):
    """rider_delivery_demo_0904 评分器。"""

    def grade(self, messages: list[TraceMessage], task: TaskDefinition,
              rubrics: list[Rubric], judge=None) -> GradingResult:
        conversation = self.format_conversation(messages)
        rubric_scores: list[RubricScore] = []
        violations: list[Violation] = []

        for r in rubrics:
            if not self._is_triggered(r.trigger, messages):
                rubric_scores.append(RubricScore(
                    rubric_id=r.id, dimension=r.dimension, method=r.method,
                    weight=r.weight, triggered=False, score=0.0,
                    reasoning="触发条件未满足,不计入评分",
                ))
                continue

            if r.method == "llm_judge" and judge is None:
                rubric_scores.append(RubricScore(
                    rubric_id=r.id, dimension=r.dimension, method=r.method,
                    weight=r.weight, triggered=False, score=0.0,
                    reasoning="未提供 LLM Judge,跳过语义项",
                ))
                continue

            score, reasoning, ev_turn, vios = self._dispatch_rubric(
                r, messages, task, conversation, judge)
            for v in vios:
                v.rubric_id = r.id
            violations.extend(vios)
            rubric_scores.append(RubricScore(
                rubric_id=r.id, dimension=r.dimension, method=r.method,
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
