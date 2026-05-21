"""Persona 三层模型 —— 性格 + 剧本 + 噪音。

- 性格 Personality:任务无关,跨任务复用,决定「怎么说」(语气/用词)。
  放 personalities/<id>.yaml。
- 剧本 PersonaScript:任务专属,决定「测哪条逻辑分支」(状态机 + 探针)。
  放 tasks/<task>/personas/<name>.yaml,引用一个性格 + 一个噪音档。
- 噪音 NoiseProfile:正交档位,决定「输入有多脏」(口语噪音 / ASR 错误)。
  放 configs/noise_profiles.yaml。

运行时 persona = 性格 + 剧本 + 噪音 合成为一个 Persona 对象。
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


class NoiseProfile(BaseModel):
    """噪音档位 —— 注入模拟器 prompt,让用户输入更接近真实(脏)。"""

    id: str
    name: str
    instruction: str = ""        # clean 档为空串


class ProbeConfig(BaseModel):
    """定向探针 —— 在第 N 个用户轮强制注入一句话。"""

    id: str
    inject_at_turn: int
    text: str
    description: str = ""


class PersonaScript(BaseModel):
    """任务剧本 —— 任务专属的状态机 + 探针;引用一个性格 + 一个噪音档。"""

    id: str
    personality: str                     # 引用 personalities/<id>.yaml
    noise: str = "clean"                 # 引用噪音档位
    name: str = ""                       # 可选显示名
    states: dict[str, str]
    initial_state: str
    transitions: dict[str, str]
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
    # —— 来自噪音层 ——
    noise_id: str = "clean"
    noise_instruction: str = ""
    # —— 来自剧本层 ——
    states: dict[str, str]
    initial_state: str
    transitions: dict[str, str]
    probes: list[ProbeConfig] = Field(default_factory=list)
    max_rounds: int = 12


def load_personality(path: str | Path) -> Personality:
    with open(path, encoding="utf-8") as f:
        return Personality.model_validate(yaml.safe_load(f))


def load_noise_profiles(path: str | Path) -> dict[str, NoiseProfile]:
    """读 configs/noise_profiles.yaml → {id: NoiseProfile}。"""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {k: NoiseProfile(id=k, **v) for k, v in data.items()}


def load_persona(script_path: str | Path,
                 personalities_dir: str | Path | None = None,
                 noise_file: str | Path | None = None) -> Persona:
    """加载一个 persona:剧本 + 引用的性格 + 引用的噪音档,合成运行时对象。

    personalities_dir / noise_file 不传时,按目录结构推断
    (script 位于 <root>/tasks/<task>/personas/<name>.yaml)。
    """
    script_path = Path(script_path).resolve()
    with open(script_path, encoding="utf-8") as f:
        script = PersonaScript.model_validate(yaml.safe_load(f))

    root = script_path.parents[3]        # personas → task → tasks → root
    pdir = Path(personalities_dir) if personalities_dir else root / "personalities"
    nfile = Path(noise_file) if noise_file else root / "configs" / "noise_profiles.yaml"

    personality = load_personality(pdir / f"{script.personality}.yaml")

    noise_instruction = ""
    if Path(nfile).exists():
        profiles = load_noise_profiles(nfile)
        if script.noise in profiles:
            noise_instruction = profiles[script.noise].instruction

    return Persona(
        id=script.id,
        name=script.name or f"{personality.name}·{script.id}",
        personality_id=personality.id,
        description=personality.description,
        speaking_style=personality.speaking_style,
        noise_id=script.noise,
        noise_instruction=noise_instruction,
        states=script.states,
        initial_state=script.initial_state,
        transitions=script.transitions,
        probes=script.probes,
        max_rounds=script.max_rounds,
    )
