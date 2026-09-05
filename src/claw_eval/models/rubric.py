"""Rubric 数据模型 —— 评测检查项。

字段为两个任务共用契约,留好扩展位。
新增字段(向后兼容):
  category   —— 7 类语义分类,便于人审分级
  confidence —— LLM 抽取器返回的置信度
  reviewed   —— 是否经人审,safety 类未审不应转正
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

# 7 类语义分类 —— 抽取器按此拆,人审按此分级处理
KNOWN_CATEGORIES = {
    "opening", "flow", "faq", "constraint",
    "role", "behavior", "safety",
}


TriggerType = Literal["probe", "user_state", "user_keyword"]
RubricDimension = Literal["completion", "robustness", "safety"]
RubricMethod = Literal[
    "length", "placeholder", "keyword", "number_whitelist",
    "ordered_keyword", "pace_checker", "blacklist", "llm_judge",
]
RubricCategory = Literal[
    "opening", "flow", "faq", "constraint", "role", "behavior", "safety",
]


class TriggerSpec(BaseModel):
    """触发条件 —— 仅当条件满足时该 rubric 才计分。

    type:
      - probe         : 某探针被注入过(probe_id)
      - user_state    : 用户模拟器到达过某状态(state)
      - user_keyword  : 用户话术里出现过任一关键词(keywords)
    """

    model_config = ConfigDict(extra="forbid")

    type: TriggerType
    desc: str = ""
    probe_id: str | None = None
    state: str | None = None
    keywords: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_payload(self) -> "TriggerSpec":
        required = {
            "probe": (self.probe_id, "probe_id"),
            "user_state": (self.state, "state"),
            "user_keyword": (self.keywords, "keywords"),
        }
        value, field_name = required[self.type]
        if not value:
            raise ValueError(f"trigger type={self.type} 必须提供 {field_name}")
        return self


class Rubric(BaseModel):
    """一条原子化、可判定的检查项。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    dimension: RubricDimension
    method: RubricMethod
    check: str                           # 检查描述;method=llm_judge 时即评委评分提示
    weight: float = Field(default=0.0, ge=0.0, allow_inf_nan=False)
    trigger: TriggerSpec | None = None   # None = 始终检查
    is_safety: bool = False              # True = 违反则 task_score 归零
    params: dict[str, Any] = Field(default_factory=dict)
    # —— 抽取器 + 人审相关字段(向后兼容)——
    category: RubricCategory | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reviewed: bool = False               # 是否经人审

    @model_validator(mode="after")
    def normalize_blacklist(self):
        if self.method == "blacklist":
            params = dict(self.params)
            words = []
            for key in ("banned_words", "blacklist", "keywords"):
                value = params.pop(key, [])
                if not isinstance(value, list):
                    raise ValueError("黑名单必须是字符串列表")
                words.extend(value)
            if words:
                params["banned_words"] = list(dict.fromkeys(words))
            self.params = params
        return self


def load_rubrics(path: str | Path) -> list[Rubric]:
    """从 rubrics.yaml 加载 rubric 列表。"""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return [Rubric.model_validate(r) for r in data["rubrics"]]


def save_rubrics(rubrics: list[Rubric], path: str | Path,
                 include_meta: bool = True) -> None:
    """写 rubric YAML(顶层 rubrics: 列表)。

    include_meta=False 时不写抽取元信息(category/confidence/reviewed),
    保持精简,适合手维护的 rubrics.yaml。
    """
    path = Path(path)
    out_list = []
    for r in rubrics:
        d = r.model_dump(exclude_none=True)
        if not include_meta:
            for k in ("category", "confidence", "reviewed"):
                d.pop(k, None)
        # 默认值的字段去掉,YAML 更干净
        if d.get("reviewed") is False:
            d.pop("reviewed", None)
        if d.get("is_safety") is False:
            d.pop("is_safety", None)
        if d.get("params") == {}:
            d.pop("params", None)
        out_list.append(d)
    path.write_text(
        yaml.safe_dump({"rubrics": out_list}, allow_unicode=True,
                       sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
