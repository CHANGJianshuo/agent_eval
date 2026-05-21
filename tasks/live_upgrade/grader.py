"""课程发布平台 · 直播升级通知外呼任务评分器(claw-eval 风格)。

编排逻辑与美团任务同构,差异在 rubrics.yaml(7 步流程、节奏检测、黑名单等)。
通过 AbstractGrader._dispatch_rubric 复用 8 类标准 method(含 ordered_keyword /
pace_checker / blacklist 等本任务新增的)。
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


class LiveUpgradeGrader(AbstractGrader):
    """直播升级外呼评分器。"""

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
        return True
