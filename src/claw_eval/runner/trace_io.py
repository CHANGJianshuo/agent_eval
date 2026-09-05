"""Trace JSONL 读写。评分完全基于 trace,可复现可审计。

事件类型:dialogue_start / turn / dialogue_end。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..models.trace import TraceMessage


class TraceWriter:
    """逐事件写 JSONL。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "x", encoding="utf-8")

    def write(self, event: dict) -> None:
        self._fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "TraceWriter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def load_trace(path: str | Path) -> tuple[dict, list[TraceMessage], dict]:
    """读 trace 文件 → (dialogue_start 事件, 消息列表, dialogue_end 事件)。"""
    start: dict = {}
    end: dict = {}
    messages: list[TraceMessage] = []

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            kind = ev.get("event")
            if kind == "dialogue_start":
                start = ev
            elif kind == "dialogue_end":
                end = ev
            elif kind == "turn":
                messages.append(TraceMessage(
                    turn=ev["turn"],
                    role=ev["role"],
                    text=ev["text"],
                    state=ev.get("state"),
                    is_probe=ev.get("is_probe", False),
                    probe_id=ev.get("probe_id"),
                ))
    return start, messages, end
