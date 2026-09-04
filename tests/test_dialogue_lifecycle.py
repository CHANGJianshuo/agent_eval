"""Failure-path coverage for trace and batch lifecycle state."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from claw_eval.cli import _batch_completion_status
from claw_eval.models.persona import Persona
from claw_eval.models.task import TaskDefinition
from claw_eval.runner import dialogue_loop


def test_batch_completion_status_is_truthful():
    assert _batch_completion_status(3, 3) == "done"
    assert _batch_completion_status(3, 2) == "partial"
    assert _batch_completion_status(3, 0) == "failed"
    assert _batch_completion_status(0, 0) == "failed"


def test_failed_dialogue_still_writes_terminal_event(tmp_path: Path,
                                                      monkeypatch):
    class StubSUT:
        def __init__(self, *args, **kwargs):
            pass

        def open_call(self):
            return "你好，我是站长。"

        def respond(self, messages):
            return "不会执行到这里"

    class FailingSimulator:
        def __init__(self, *args, **kwargs):
            pass

        def next(self, messages):
            raise RuntimeError("simulator unavailable")

    monkeypatch.setattr(dialogue_loop, "SUTAdapter", StubSUT)
    monkeypatch.setattr(dialogue_loop, "UserSimulator", FailingSimulator)
    task = TaskDefinition(task_id="demo", prompt="你好")
    persona = Persona(
        id="p1",
        name="P1",
        personality_id="generic",
        description="测试用户",
        speaking_style="简短",
        scenario="接听电话",
    )
    trace = tmp_path / "failed.jsonl"

    with pytest.raises(RuntimeError, match="simulator unavailable"):
        dialogue_loop.run_dialogue(
            task,
            persona,
            sut_model="stub",
            simulator_model="stub",
            trace_path=trace,
        )

    events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == [
        "dialogue_start", "turn", "dialogue_error", "dialogue_end",
    ]
    assert events[1]["role"] == "assistant"
    assert events[1]["turn"] == 1
    assert events[-1]["end_reason"] == "error"
