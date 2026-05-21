"""任务流程图数据模型 —— 每任务一份 flow.yaml,节点关联到 rubric。

dashboard 渲染时按 rubric 的 pass 率给节点上色:
- 任务详情页:节点色 = 该 rubric 跨 case 平均得分
- 单 case 报告:节点色 = 该 case 该 rubric 的得分
"""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class FlowNode(BaseModel):
    id: str
    label: str
    rubric: str | None = None            # 关联 rubric id;None = 中性节点(如 start/end)
    optional: bool = False               # 触发型节点(分支)
    x: float | None = None               # 手工坐标(可选;否则自动布局)
    y: float | None = None


class FlowDiagram(BaseModel):
    nodes: list[FlowNode]
    edges: list[list[str]] = Field(default_factory=list)


def load_flow(path: str | Path) -> FlowDiagram | None:
    """读 flow.yaml;文件不存在返回 None(任务没配流程图)。"""
    path = Path(path)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return FlowDiagram.model_validate(data)
