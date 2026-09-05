"""Trace 数据模型 —— 对话消息 + 评分结果。

trace 以 JSONL 落盘,评分完全基于 trace,保证可复现、可审计、可重放。
"""
from __future__ import annotations

from typing import Literal
import math

from pydantic import BaseModel, Field, model_validator


class TraceMessage(BaseModel):
    """一轮对话消息。user=骑手(用户模拟器),assistant=站长(SUT)。"""

    turn: int
    role: Literal["user", "assistant"]
    text: str
    state: str | None = None       # 该轮用户模拟器所处状态(role=user 时有意义)
    is_probe: bool = False         # 是否为探针强制注入的话术
    probe_id: str | None = None


class DimensionScores(BaseModel):
    """三维度得分。safety 是 0/1 乘子。"""

    completion: float = 0.0
    robustness: float = 0.0
    safety: float = 1.0


class Violation(BaseModel):
    """一条违规记录 —— 指向具体轮次,带证据,是「可解释」的载体。"""

    rubric_id: str = ""
    turn: int | None = None
    detail: str = ""
    evidence: str = ""


class RubricScore(BaseModel):
    """单条 rubric 的评分结果。"""

    rubric_id: str
    dimension: str
    method: str
    weight: float
    is_safety: bool = False
    triggered: bool = True         # 触发型 rubric 未触发则 False,不计入分母
    score: float = Field(default=0.0, ge=0, le=1, allow_inf_nan=False)
    reasoning: str = ""
    evidence_turn: int | None = None
    status: Literal["scored", "not_applicable", "skipped", "error"] = "scored"

    @model_validator(mode="before")
    @classmethod
    def read_legacy_status(cls, data):
        if isinstance(data, dict) and "status" not in data and not data.get("triggered", True):
            data = dict(data)
            data["status"] = ("skipped" if "未提供 LLM Judge" in data.get("reasoning", "")
                              else "not_applicable")
        return data


class GradingResult(BaseModel):
    """一次评分的完整结果 —— 即评测报告的数据源。"""

    task_id: str
    persona_id: str = ""
    dimension_scores: DimensionScores
    task_score: float | None = Field(ge=0, le=1, allow_inf_nan=False)
    passed: bool | None
    status: Literal["complete", "incomplete", "error"] = "complete"
    rubric_scores: list[RubricScore] = Field(default_factory=list)
    violations: list[Violation] = Field(default_factory=list)
    trace_path: str | None = None
    script_id: str = ""
    # 当 batch 用 --dimensions 模式时,记录该 case 实际采到的维度组合
    demographics: dict[str, str] = Field(default_factory=dict)
    run_id: str = ""
    input_hash: str = ""
    case_id: str = ""
    error_message: str = ""

    @model_validator(mode="after")
    def require_complete_grading(self):
        if any(r.status == "scored" and r.dimension != "safety" and not r.is_safety
               and (r.weight <= 0 or not math.isfinite(r.weight))
               for r in self.rubric_scores):
            self.status = "error"
            self.error_message = "非安全评分项必须有正的有限权重，不能据此给出通过结论"
        # Also correct legacy no-judge results on read. Custom graders share this guard.
        if any(r.status == "error" for r in self.rubric_scores):
            self.status = "error"
        elif self.rubric_scores and (any(r.status == "skipped" for r in self.rubric_scores)
                                   or not any(r.status == "scored" for r in self.rubric_scores)):
            self.status = "incomplete"
        if self.status != "complete" or self.task_score is None:
            self.task_score = None
            self.passed = None
            if self.status == "complete":
                self.status = "incomplete"
        return self
