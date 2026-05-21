"""Rubric 数据模型 —— 评测检查项。从 tasks/<id>/rubrics.yaml 加载。

字段为两个任务(飞毛腿 / 直播升级)共用契约,留好扩展位。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class TriggerSpec(BaseModel):
    """触发条件 —— 仅当条件满足时该 rubric 才计分。

    type:
      - probe         : 某探针被注入过(probe_id)
      - user_state    : 用户模拟器到达过某状态(state)
      - user_keyword  : 用户话术里出现过任一关键词(keywords)
    """

    type: str
    desc: str = ""
    probe_id: str | None = None
    state: str | None = None
    keywords: list[str] = Field(default_factory=list)


class Rubric(BaseModel):
    """一条原子化、可判定的检查项。"""

    id: str
    dimension: str                       # completion | robustness | safety
    method: str                          # length|placeholder|keyword|number_whitelist|llm_judge
    check: str                           # 检查描述;method=llm_judge 时即评委评分提示
    weight: float = 0.0
    trigger: TriggerSpec | None = None   # None = 始终检查
    is_safety: bool = False              # True = 违反则 task_score 归零
    params: dict[str, Any] = Field(default_factory=dict)


def load_rubrics(path: str | Path) -> list[Rubric]:
    """从 rubrics.yaml 加载 rubric 列表。"""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return [Rubric.model_validate(r) for r in data["rubrics"]]
