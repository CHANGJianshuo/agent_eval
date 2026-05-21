"""Rubric 抽取器单测 —— 解析 / 保存 / 调用编排(不真调 LLM)。"""
from __future__ import annotations

from pathlib import Path

import pytest

from claw_eval.models.rubric import Rubric, save_rubrics, load_rubrics
from claw_eval.models.task import TaskDefinition
from claw_eval.rubric.extractor import (
    build_prompt,
    extract_rubrics,
    parse_extractor_output,
)


# ----------------------------- 解析 -----------------------------

_VALID = """\
- id: opening.greeting
  category: opening
  dimension: completion
  method: keyword
  weight: 0.08
  check: 开场白必含「站长」「飞毛腿」
  params: {scope: first_assistant, keywords: [站长, 飞毛腿], mode: all}
  confidence: 0.95
- id: safety.no_hallucinated_numbers
  category: safety
  dimension: safety
  method: number_whitelist
  weight: 1.0
  is_safety: true
  check: 不编造任务变量外的数字
  confidence: 0.97
"""


def test_parse_basic_list():
    out = parse_extractor_output(_VALID)
    assert len(out) == 2
    assert out[0].id == "opening.greeting"
    assert out[0].category == "opening"
    assert out[0].confidence == 0.95
    assert out[1].is_safety is True


def test_parse_with_markdown_codeblock():
    """LLM 经常用 ```yaml 包装。"""
    wrapped = f"```yaml\n{_VALID}```"
    out = parse_extractor_output(wrapped)
    assert len(out) == 2
    assert out[0].id == "opening.greeting"


def test_parse_with_rubrics_dict_wrapper():
    """有时 LLM 会包成 {rubrics: [...]}。"""
    wrapped = f"rubrics:\n{_VALID}"
    out = parse_extractor_output(wrapped)
    assert len(out) == 2


def test_parse_rejects_non_list():
    with pytest.raises(ValueError, match="rubric 列表"):
        parse_extractor_output("not a list at all")


def test_parse_propagates_field_errors():
    """缺必需字段(id) → 抛清晰错误。"""
    bad = "- category: opening\n  dimension: completion\n  method: keyword\n  check: x\n"
    with pytest.raises(ValueError, match="第 1 条"):
        parse_extractor_output(bad)


# --------------------------- prompt 构造 ---------------------------

def test_build_prompt_includes_variables_and_task():
    task = TaskDefinition(
        task_id="t", prompt="Hello {X}", variables={"X": 20, "Y": "test"})
    p = build_prompt(task)
    assert "Hello {X}" in p          # raw prompt 原样
    assert "X = 20" in p             # 变量真值列出
    assert "Y = test" in p


def test_build_prompt_no_variables():
    task = TaskDefinition(task_id="t", prompt="Hello", variables={})
    p = build_prompt(task)
    assert "(无)" in p


# ------------------------- extract_rubrics 编排 -------------------------

def test_extract_rubrics_calls_llm_and_parses(monkeypatch):
    """mock LLM 返回,验证 extract_rubrics 串起 chat → parse。"""
    captured: dict = {}

    def fake_chat(model, messages, temperature=0.7, **kwargs):
        captured["model"] = model
        captured["messages"] = messages
        captured["reasoning_effort"] = kwargs.get("reasoning_effort")
        return _VALID

    monkeypatch.setattr(
        "claw_eval.rubric.extractor.llm_client.chat", fake_chat)

    task = TaskDefinition(task_id="t", prompt="P", variables={"X": 1})
    out = extract_rubrics(task, "fake-model", reasoning_effort="low")

    assert captured["model"] == "fake-model"
    assert captured["reasoning_effort"] == "low"
    assert len(out) == 2
    assert out[0].id == "opening.greeting"


# --------------------------- save/load 往返 ---------------------------

def test_save_and_reload_preserves_meta(tmp_path: Path):
    rubrics = parse_extractor_output(_VALID)
    p = tmp_path / "rubrics.draft.yaml"
    save_rubrics(rubrics, p, include_meta=True)
    loaded = load_rubrics(p)
    assert loaded[0].category == "opening"
    assert loaded[0].confidence == 0.95


def test_save_without_meta_strips_extraction_fields(tmp_path: Path):
    rubrics = parse_extractor_output(_VALID)
    p = tmp_path / "rubrics.yaml"
    save_rubrics(rubrics, p, include_meta=False)
    text = p.read_text(encoding="utf-8")
    assert "confidence" not in text
    assert "category" not in text
    # 仍能正常重新加载
    loaded = load_rubrics(p)
    assert loaded[0].id == "opening.greeting"
