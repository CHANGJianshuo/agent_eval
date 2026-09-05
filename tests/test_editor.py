"""Persona 编辑器单测 —— 测纯函数(DOT 生成 + YAML 序列化),不依赖 streamlit。"""
from __future__ import annotations

import yaml

from claw_eval.editor.dot import build_dot
from claw_eval.models.persona import NoiseSpec, PersonaScript, ProbeConfig


# ----------------------------- DOT 生成 -----------------------------

def test_build_dot_basic_structure():
    dot = build_dot(
        states=["A", "B"],
        transitions=[("A", "B"), ("B", "END")],
        initial_state="A",
    )
    assert "digraph G {" in dot
    assert '"A" -> "B"' in dot
    assert '"B" -> "END"' in dot
    assert "END" in dot                          # 终态节点存在


def test_build_dot_highlights_initial():
    dot = build_dot(["A", "B"], [("A", "B")], "A")
    # 初始节点应该被特别样式
    assert "#3370ff" in dot                      # 蓝色


def test_build_dot_end_styled():
    dot = build_dot(["A"], [("A", "END")], "A")
    assert "#22c55e" in dot                      # END 绿色


def test_build_dot_skips_blank_transitions():
    dot = build_dot(["A"], [("", ""), ("A", "END")], "A")
    # 空转移被跳过,只剩一条
    assert dot.count("->") == 1


# ----------------------------- YAML 往返 -----------------------------

def test_persona_script_yaml_roundtrip():
    """编辑器保存的 YAML 能被 load_persona 读回(关键的可复用性)。"""
    s1 = PersonaScript(
        id="test",
        personality="cooperative",
        noise=NoiseSpec(rate=0.25, kinds=["filler"]),
        name="测试型",
        states={"接听": "应答"},
        initial_state="接听",
        transitions={"接听": "END"},
        probes=[ProbeConfig(id="p", inject_at_turn=2, text="测试", description="d")],
        max_rounds=5,
    )
    text = yaml.safe_dump(s1.model_dump(), allow_unicode=True, sort_keys=False)
    s2 = PersonaScript.model_validate(yaml.safe_load(text))
    assert s2.id == s1.id
    assert s2.personality == s1.personality
    assert s2.noise.rate == 0.25
    assert s2.noise.kinds == ["filler"]
    assert s2.states == s1.states
    assert len(s2.probes) == 1
    assert s2.probes[0].inject_at_turn == 2
