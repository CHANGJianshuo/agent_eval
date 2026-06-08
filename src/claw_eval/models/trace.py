"""Trace 数据模型 —— 对话消息 + 评分结果。

trace 以 JSONL 落盘,评分完全基于 trace,保证可复现、可审计、可重放。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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
    triggered: bool = True         # 触发型 rubric 未触发则 False,不计入分母
    score: float = 0.0
    reasoning: str = ""
    evidence_turn: int | None = None


class GradingResult(BaseModel):
    """一次评分的完整结果 —— 即评测报告的数据源。"""

    task_id: str
    persona_id: str = ""
    dimension_scores: DimensionScores
    task_score: float
    passed: bool
    rubric_scores: list[RubricScore] = Field(default_factory=list)
    violations: list[Violation] = Field(default_factory=list)
    trace_path: str | None = None
    script_id: str = ""
    # 当 batch 用 --dimensions 模式时,记录该 case 实际采到的维度组合
    demographics: dict[str, str] = Field(default_factory=dict)
