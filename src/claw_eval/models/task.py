"""TaskDefinition —— 从 task.yaml 加载被测任务。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class TaskDefinition(BaseModel):
    """一个被评测任务的定义。"""

    task_id: str
    task_name: str = ""
    prompt: str                          # 任务 Prompt 模板(含 {变量})
    variables: dict[str, Any] = Field(default_factory=dict)
    max_rounds: int = 12
    task_dir: str | None = Field(default=None, exclude=True)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "TaskDefinition":
        """读 YAML → Pydantic 校验。"""
        path = Path(path)
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        data["task_dir"] = str(path.parent.resolve())
        return cls.model_validate(data)

    def rendered_prompt(self) -> str:
        """把变量代入 Prompt 模板 —— SUT 收到的是代入真值后的 Prompt。"""
        try:
            return self.prompt.format(**self.variables)
        except (KeyError, IndexError, ValueError):
            return self.prompt

    def number_whitelist(self) -> list[str]:
        """任务允许出现的数字 = 所有数值型变量。供数字白名单 matcher 使用。"""
        return [str(v) for v in self.variables.values()
                if isinstance(v, (int, float)) and not isinstance(v, bool)]
