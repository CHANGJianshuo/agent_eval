"""比例分配采样 —— 按 weights 把 N 个 trial 分配到各 persona,反映真实流量分布。

batch 命令 `--total N` 时,从 `tasks/<task>/sampling.yaml` 读 weights,
用「大余数法(Hare quota)」分配,保证 sum 严格 = N。
"""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class SamplingConfig(BaseModel):
    """tasks/<task>/sampling.yaml 的结构。"""

    weights: dict[str, float] = Field(default_factory=dict)


def load_sampling(path: str | Path) -> SamplingConfig:
    """读 sampling.yaml → SamplingConfig。"""
    with open(path, encoding="utf-8") as f:
        return SamplingConfig.model_validate(yaml.safe_load(f) or {})


def allocate(weights: dict[str, float], total: int) -> dict[str, int]:
    """大余数法(Hare quota):按 weights 分配 total,sum 严格 = total。

    - weights 任意正数,不要求和为 100
    - weight=0 / 负数 的 persona 分配 0
    - total<=0 时全 0
    """
    valid = {k: v for k, v in weights.items() if v > 0}
    total_w = sum(valid.values())
    if total_w <= 0 or total <= 0:
        return {k: 0 for k in weights}

    shares = {k: total * v / total_w for k, v in valid.items()}
    out = {k: int(s) for k, s in shares.items()}
    remaining = total - sum(out.values())

    if remaining > 0:
        # 小数部分大者优先;同分按 key 字母序保证可复现
        fracs = sorted(
            shares.items(),
            key=lambda kv: (-(kv[1] - int(kv[1])), kv[0]),
        )
        for k, _ in fracs[:remaining]:
            out[k] += 1

    # weight=0 的 persona 显式给 0
    for k in weights:
        out.setdefault(k, 0)
    return out
