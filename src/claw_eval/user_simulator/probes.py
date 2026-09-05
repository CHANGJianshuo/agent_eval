"""定向探针 —— 在指定用户轮强制注入话术。

保证关键场景(问越权问题、坚持拒绝等)一定被触发,而不依赖 LLM 自觉。
"""
from __future__ import annotations

from ..models.persona import Persona, ProbeConfig


def probe_for_turn(persona: Persona, user_turn_index: int) -> ProbeConfig | None:
    """返回应在第 user_turn_index 个用户轮注入的探针(没有则 None)。"""
    for p in persona.probes:
        if p.inject_at_turn == user_turn_index:
            return p
    return None
