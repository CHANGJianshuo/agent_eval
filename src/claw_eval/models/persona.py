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


class Demographics(BaseModel):
    """人口学特征 —— 任务无关,作为性格底色的一部分。"""

    mbti: str = "unspecified"            # 16 类 + unspecified
    age_range: str = "unspecified"       # <20 / 20-29 / 30-39 / 40-49 / 50+ / unspecified
    gender: str = "unspecified"          # male / female / unspecified
    education: str = "unspecified"       # primary / middle / high / college / postgrad / unspecified
    attitude: str = "unspecified"        # 7 类情绪态度 + unspecified


class Personality(BaseModel):
    """性格底色 —— 任务无关,决定语气/用词,可跨任务复用。"""

    id: str
    name: str
    description: str
    speaking_style: str
    demographics: Demographics = Field(default_factory=Demographics)


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
    """任务剧本 —— 任务专属,描述一条逻辑分支路径 + 探针。与性格完全解耦。

    v2 格式:scenario(场景描述) + probes + covers_flow_nodes,无状态机。
    v1 格式(向后兼容):states + transitions + personality,仍可加载。
    """

    id: str
    name: str = ""
    scenario: str = ""                              # v2: 场景描述,告诉模拟器该走哪条逻辑分支
    covers_flow_nodes: list[str] = Field(default_factory=list)
    probes: list[ProbeConfig] = Field(default_factory=list)
    max_rounds: int = 12
    noise: NoiseSpec = Field(default_factory=NoiseSpec)
    # v1 兼容字段(新生成的剧本不再填)
    personality: str = ""                           # v1: 绑定的性格 id
    states: dict[str, str] = Field(default_factory=dict)
    initial_state: str = ""
    transitions: dict[str, str | dict[str, float]] = Field(default_factory=dict)


class Persona(BaseModel):
    """运行时合成的完整 persona = 性格 + 剧本 + 噪音。"""

    id: str
    name: str
    # —— 来自性格层 ——
    personality_id: str
    description: str
    speaking_style: str
    demographics: Demographics = Field(default_factory=Demographics)
    # —— 来自噪音层(rate 版)——
    noise_rate: float = 0.0
    noise_kinds: list[NoiseKind] = Field(default_factory=list)
    # —— 来自剧本层(v2: scenario; v1: states/transitions) ——
    script_id: str = ""
    scenario: str = ""
    states: dict[str, str] = Field(default_factory=dict)
    initial_state: str = ""
    transitions: dict[str, str | dict[str, float]] = Field(default_factory=dict)
    probes: list[ProbeConfig] = Field(default_factory=list)
    max_rounds: int = 12
    covers_flow_nodes: list[str] = Field(default_factory=list)

    @property
    def is_v2(self) -> bool:
        return bool(self.scenario)


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

    if script.personality:
        personality = load_personality(pdir / f"{script.personality}.yaml")
        p_id = personality.id
        p_name = personality.name
        p_desc = personality.description
        p_style = personality.speaking_style
        p_demo = personality.demographics
    else:
        p_id = "generic"
        p_name = script.name or script.id
        p_desc = "你是一位真实用户。"
        p_style = "自然口语,简短礼貌。"
        p_demo = Demographics()

    noise_kinds: list[NoiseKind] = []
    if script.noise.kinds and Path(nfile).exists():
        library = load_noise_kinds(nfile)
        for kind_id in script.noise.kinds:
            if kind_id in library:
                noise_kinds.append(library[kind_id])

    return Persona(
        id=script.id,
        name=script.name or f"{p_name}·{script.id}",
        personality_id=p_id,
        description=p_desc,
        speaking_style=p_style,
        demographics=p_demo,
        noise_rate=script.noise.rate,
        noise_kinds=noise_kinds,
        scenario=script.scenario,
        states=script.states,
        initial_state=script.initial_state,
        transitions=script.transitions,
        probes=script.probes,
        max_rounds=script.max_rounds,
        covers_flow_nodes=script.covers_flow_nodes,
    )
