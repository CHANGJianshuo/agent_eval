"""Persona 三层模型 —— 性格 + 剧本 + 噪音(rate 版)。

- 性格 Personality:任务无关,跨任务复用,决定「怎么说」。
- 剧本 PersonaScript:任务专属,决定「测哪条逻辑分支」。
- 噪音 NoiseSpec (rate, kinds):
    rate  = 每个用户轮被注入噪音的概率(per-turn 掷骰,seeded)
    kinds = 噪音种类列表(引用 noise_profiles.yaml 里的种类 id)
    rate=0(默认)→ 全干净;rate=1 → 每轮都脏;0<rate<1 → 部分轮脏。

运行时 persona = 性格 + 剧本 + 噪音 合成。
"""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Personality(BaseModel):
    """性格底色 —— 任务无关,决定语气/用词,可跨任务复用。"""

    id: str
    name: str
    description: str
    speaking_style: str


class NoiseKind(BaseModel):
    """一种噪音 —— 命中时其 instruction 注入「该轮」模拟器 prompt。"""

    id: str
    name: str
    instruction: str = ""


class NoiseSpec(BaseModel):
    """剧本中的噪音规格 —— 频率(rate) + 种类(kinds 引用 NoiseKind id)。"""

    rate: float = 0.0
    kinds: list[str] = Field(default_factory=list)


class ProbeConfig(BaseModel):
    """定向探针 —— 在第 N 个用户轮强制注入一句话。"""

    id: str
    inject_at_turn: int
    text: str
    description: str = ""


class PersonaScript(BaseModel):
    """任务剧本 —— 任务专属的状态机 + 探针;引用一个性格 + 一份噪音规格。"""

    id: str
    personality: str                                # 引用 personalities/<id>.yaml
    noise: NoiseSpec = Field(default_factory=NoiseSpec)
    name: str = ""
    states: dict[str, str]
    initial_state: str
    # transitions 值两种形式:
    #   - str  → 确定性单一目标(老格式,向后兼容)
    #   - dict[str, float] → 概率多分支 {next_state: weight}
    transitions: dict[str, str | dict[str, float]]
    probes: list[ProbeConfig] = Field(default_factory=list)
    max_rounds: int = 12


class Persona(BaseModel):
    """运行时合成的完整 persona = 性格 + 剧本 + 噪音。"""

    id: str
    name: str
    # —— 来自性格层 ——
    personality_id: str
    description: str
    speaking_style: str
    # —— 来自噪音层(rate 版)——
    noise_rate: float = 0.0
    noise_kinds: list[NoiseKind] = Field(default_factory=list)
    # —— 来自剧本层 ——
    states: dict[str, str]
    initial_state: str
    transitions: dict[str, str | dict[str, float]]
    probes: list[ProbeConfig] = Field(default_factory=list)
    max_rounds: int = 12


def load_personality(path: str | Path) -> Personality:
    with open(path, encoding="utf-8") as f:
        return Personality.model_validate(yaml.safe_load(f))


def load_noise_kinds(path: str | Path) -> dict[str, NoiseKind]:
    """读 configs/noise_profiles.yaml → {id: NoiseKind}(噪音「种类库」)。"""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {k: NoiseKind(id=k, **v) for k, v in data.items()}


def load_persona(script_path: str | Path,
                 personalities_dir: str | Path | None = None,
                 noise_file: str | Path | None = None) -> Persona:
    """加载剧本 + 性格 + 噪音种类,合成运行时 Persona。"""
    script_path = Path(script_path).resolve()
    with open(script_path, encoding="utf-8") as f:
        script = PersonaScript.model_validate(yaml.safe_load(f))

    root = script_path.parents[3]
    pdir = Path(personalities_dir) if personalities_dir else root / "personalities"
    nfile = Path(noise_file) if noise_file else root / "configs" / "noise_profiles.yaml"

    personality = load_personality(pdir / f"{script.personality}.yaml")

    noise_kinds: list[NoiseKind] = []
    if script.noise.kinds and Path(nfile).exists():
        library = load_noise_kinds(nfile)
        for kind_id in script.noise.kinds:
            if kind_id in library:
                noise_kinds.append(library[kind_id])

    return Persona(
        id=script.id,
        name=script.name or f"{personality.name}·{script.id}",
        personality_id=personality.id,
        description=personality.description,
        speaking_style=personality.speaking_style,
        noise_rate=script.noise.rate,
        noise_kinds=noise_kinds,
        states=script.states,
        initial_state=script.initial_state,
        transitions=script.transitions,
        probes=script.probes,
        max_rounds=script.max_rounds,
    )
