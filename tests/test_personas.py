"""Persona 三层加载 + 噪音 rate 单测 —— 不依赖 API。

剧本 fixture 放 tests/fixtures/personas_v1/(v1 状态机格式),
不直接依赖 tasks/ 下的活配置 —— 任务内容会随评测演化,测试不该跟着碎。
v2 格式(scenario 自然语言剧本)单独造最小 YAML 测。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from claw_eval.models.persona import (
    NoiseKind,
    Persona,
    ProbeConfig,
    load_noise_kinds,
    load_persona,
    load_personality,
)
from claw_eval.user_simulator.simulator import UserSimulator
from claw_eval.user_simulator.state_machine import StateMachine

_ROOT = Path(__file__).resolve().parents[1]
_PERS = _ROOT / "personalities"
_NOISE = _ROOT / "configs" / "noise_profiles.yaml"
_FIXTURES = Path(__file__).parent / "fixtures" / "personas_v1"

_ALL_V1 = ["cooperative", "refuse", "cooperative_owner"]


def _load(persona: str):
    return load_persona(
        _FIXTURES / f"{persona}.yaml",
        personalities_dir=_PERS, noise_file=_NOISE)


# ----------------------------- 性格层 -----------------------------

def test_personality_files_load():
    for pid in ["cooperative", "refuse", "hesitant", "argumentative",
                "confused", "blunt", "hurried"]:
        p = load_personality(_PERS / f"{pid}.yaml")
        assert p.id == pid
        assert p.description and p.speaking_style


# --------------------------- 噪音种类库 ---------------------------

def test_noise_kinds_library():
    kinds = load_noise_kinds(_NOISE)
    # 至少有这 4 种
    for kid in ("filler", "asr_error", "broken", "interrupt"):
        assert kid in kinds
        assert kinds[kid].instruction      # 每种都有 instruction 文字


# ------------------------- 三层合成 persona -------------------------

@pytest.mark.parametrize("persona", _ALL_V1)
def test_persona_merges_three_layers(persona: str):
    p = _load(persona)
    # 剧本层
    assert p.states and p.initial_state and p.transitions
    # 性格层(合成进来了)
    assert p.description and p.speaking_style and p.personality_id
    # 噪音层 —— 默认 rate=0, kinds=[](全干净)
    assert p.noise_rate == 0.0
    assert p.noise_kinds == []


@pytest.mark.parametrize("persona", _ALL_V1)
def test_every_persona_state_machine_terminates(persona: str):
    sm = StateMachine(_load(persona))
    seen = [sm.current]
    for _ in range(30):
        if sm.advance():
            break
        seen.append(sm.current)
    assert sm.finished, f"{persona} 状态机未终止: {seen}"


def test_personality_is_reused_across_scripts():
    """同一性格被多任务剧本复用 —— 三层拆分的核心收益。"""
    mt = _load("cooperative")
    lv = _load("cooperative_owner")
    assert mt.personality_id == lv.personality_id == "cooperative"
    assert mt.description == lv.description
    assert mt.states != lv.states


# --------------------- v2 剧本(scenario 自然语言) ---------------------

def test_v2_script_without_personality_loads(tmp_path):
    """v2 剧本只有 scenario,没有 personality/states —— 必须能加载。

    回归保护:曾因 load_persona 强制读 personality 文件,
    v2 剧本全部静默加载失败(find_all_scripts 吞异常)。
    """
    f = tmp_path / "happy_path.yaml"
    f.write_text(
        "id: happy_path\n"
        "name: 全程配合\n"
        "scenario: 你是机构负责人,全程配合客服走完通知流程。\n"
        "max_rounds: 10\n",
        encoding="utf-8")
    p = load_persona(f, personalities_dir=_PERS, noise_file=_NOISE)
    assert p.id == "happy_path"
    assert p.scenario
    assert p.description          # 有兜底人设
    assert p.speaking_style


def test_v2_script_with_probes(tmp_path):
    f = tmp_path / "probe_script.yaml"
    f.write_text(
        "id: probe_script\n"
        "scenario: 中途问超范围问题。\n"
        "probes:\n"
        "  - id: ask_oob\n"
        "    inject_at_turn: 3\n"
        "    text: 你们倒闭了我的钱退吗?\n"
        "    description: 越界问题\n",
        encoding="utf-8")
    p = load_persona(f, personalities_dir=_PERS, noise_file=_NOISE)
    assert len(p.probes) == 1
    assert p.probes[0].inject_at_turn == 3


# ---------------------- 噪音 rate(per-turn 掷骰)----------------------

def _make_persona(noise_rate: float, kinds: list[NoiseKind]) -> Persona:
    return Persona(
        id="t", name="t", personality_id="p",
        description="d", speaking_style="s",
        noise_rate=noise_rate, noise_kinds=kinds,
        states={"接听": "..."}, initial_state="接听",
        transitions={"接听": "END"}, probes=[], max_rounds=5,
    )


_DIRTY = NoiseKind(id="dirty", name="d", instruction="dirty-instruction-XYZ")


def test_noise_rate_zero_never_injects():
    p = _make_persona(0.0, [_DIRTY])
    sim = UserSimulator(model="stub", persona=p, seed=42)
    for _ in range(50):
        assert sim._roll_noise() == ""


def test_noise_rate_one_always_injects():
    p = _make_persona(1.0, [_DIRTY])
    sim = UserSimulator(model="stub", persona=p, seed=42)
    for _ in range(20):
        assert "dirty-instruction-XYZ" in sim._roll_noise()


def test_noise_rate_partial_only_some_turns_dirty():
    """rate=0.5 应大约一半轮命中,不是全脏也不是全干净。"""
    p = _make_persona(0.5, [_DIRTY])
    sim = UserSimulator(model="stub", persona=p, seed=42)
    dirty = sum(1 for _ in range(200) if sim._roll_noise())
    assert 60 <= dirty <= 140, f"rate=0.5 命中数 {dirty}/200 偏离太大"


def test_noise_seed_reproducible():
    """同 seed → 同噪音命中序列(保证可复现)。"""
    p = _make_persona(0.5, [_DIRTY])
    sim1 = UserSimulator(model="stub", persona=p, seed=7)
    sim2 = UserSimulator(model="stub", persona=p, seed=7)
    pat1 = [bool(sim1._roll_noise()) for _ in range(40)]
    pat2 = [bool(sim2._roll_noise()) for _ in range(40)]
    assert pat1 == pat2


def test_noise_empty_kinds_means_clean():
    """有 rate 但 kinds 为空 → 无可注入,永远干净。"""
    p = _make_persona(1.0, [])
    sim = UserSimulator(model="stub", persona=p, seed=42)
    for _ in range(20):
        assert sim._roll_noise() == ""
