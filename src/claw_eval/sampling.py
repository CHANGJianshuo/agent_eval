"""比例分配采样 —— 按 weights 把 N 个 trial 分配到各 persona,反映真实流量分布。

batch 命令 `--total N` 时,从 `tasks/<task>/sampling.yaml` 读 weights,
用「大余数法(Hare quota)」分配,保证 sum 严格 = N。
"""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class NoiseOverlay(BaseModel):
    """sampling 层的噪音 overlay —— 与「用户类型」正交的环境因素。

    rate=0.1 表示在分配出来的 trial 中,**整通带噪的 case** 占 10%。
    命中 overlay 的 case 该通对话全程 noise_rate=1.0,kinds 用 overlay 指定的。
    """

    rate: float = Field(default=0.0, ge=0, le=1, allow_inf_nan=False)
    kinds: list[str] = Field(default_factory=list)


class SamplingConfig(BaseModel):
    """tasks/<task>/sampling.yaml 的结构。"""

    weights: dict[str, float] = Field(default_factory=dict)
    noise_overlay: NoiseOverlay = Field(default_factory=NoiseOverlay)


def load_sampling(path: str | Path) -> SamplingConfig:
    """读 sampling.yaml → SamplingConfig。"""
    with open(path, encoding="utf-8") as f:
        return SamplingConfig.model_validate(yaml.safe_load(f) or {})


def save_sampling(cfg: SamplingConfig, path: str | Path) -> None:
    """SamplingConfig → YAML(供编辑器写回)。"""
    data: dict = {"weights": cfg.weights}
    if cfg.noise_overlay.rate > 0 or cfg.noise_overlay.kinds:
        data["noise_overlay"] = cfg.noise_overlay.model_dump()
    Path(path).write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                       default_flow_style=False),
        encoding="utf-8",
    )


def select_noise_cases(n_total: int, rate: float, seed: int) -> set[int]:
    """从 n_total 个 trial 中选出 round(n_total × rate) 个作为「噪音 case」。

    返回噪音 case 在 [0, n_total) 内的索引集合(seeded 可复现)。
    """
    import random
    if rate <= 0 or n_total <= 0:
        return set()
    n_noise = max(0, min(n_total, round(n_total * rate)))
    rng = random.Random(seed)
    indices = list(range(n_total))
    rng.shuffle(indices)
    return set(indices[:n_noise])


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
