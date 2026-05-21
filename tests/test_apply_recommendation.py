"""apply_recommendation 单测 —— diff / 解析 / mock LLM。"""
from __future__ import annotations

from claw_eval.task_gen.apply_recommendation import (
    _strip_markdown_wrap,
    build_patch_prompt,
    diff_stats,
    generate_prompt_patch,
    unified_diff,
)


# ============================ diff ============================

def test_unified_diff_basic():
    old = "line1\nline2\nline3"
    new = "line1\nline2_modified\nline3"
    diff = unified_diff(old, new)
    assert "-line2" in diff
    assert "+line2_modified" in diff


def test_unified_diff_no_change():
    diff = unified_diff("same", "same")
    assert diff == "" or "@@" not in diff


def test_diff_stats():
    old = "a\nb\nc"
    new = "a\nB\nC"
    stats = diff_stats(old, new)
    assert stats["added"] == 2
    assert stats["removed"] == 2


def test_diff_stats_added_only():
    stats = diff_stats("a", "a\nb\nc")
    assert stats["added"] == 2
    assert stats["removed"] == 0


# ============================ prompt 构造 ============================

def test_build_patch_prompt_includes_all_fields():
    rec = {
        "rubric_id": "opening.peak_reminder",
        "avg_score": 0.19,
        "suggested_prompt_change": "加上高峰提醒",
        "rationale": "用户漏说",
    }
    system, user = build_patch_prompt("Hello world", rec)
    assert "opening.peak_reminder" in user
    assert "0.19" in user
    assert "加上高峰提醒" in user
    assert "Hello world" in user
    assert "不要 markdown" in system or "不要" in system


# ============================ strip markdown ============================

def test_strip_markdown_wrap_with_lang_tag():
    text = "```yaml\nprompt content\n```"
    assert _strip_markdown_wrap(text).strip() == "prompt content"


def test_strip_markdown_wrap_without_lang():
    text = "```\nbody\n```"
    assert _strip_markdown_wrap(text).strip() == "body"


def test_strip_markdown_no_wrap():
    text = "plain text\nno wrap"
    assert _strip_markdown_wrap(text) == text


# ============================ generate_prompt_patch ============================

def test_generate_prompt_patch_calls_llm(monkeypatch):
    captured = {}

    def fake_chat(model, messages, temperature=0.7, **kw):
        captured["model"] = model
        captured["messages"] = messages
        return "modified prompt text"

    monkeypatch.setattr(
        "claw_eval.task_gen.apply_recommendation.llm_client.chat", fake_chat)

    rec = {"rubric_id": "x", "suggested_prompt_change": "加 X"}
    out = generate_prompt_patch("original", rec, judge_model="fake")
    assert out == "modified prompt text"
    assert captured["model"] == "fake"


def test_generate_prompt_patch_strips_markdown(monkeypatch):
    monkeypatch.setattr(
        "claw_eval.task_gen.apply_recommendation.llm_client.chat",
        lambda *a, **k: "```yaml\ncleaned\n```")
    out = generate_prompt_patch("p", {"rubric_id": "x"}, judge_model="fake")
    assert out == "cleaned"
