"""可复用状态机引擎 —— 控制用户模拟器的对话走向。

引擎本身不认识任何具体 state 名,states / transitions 全部从 persona YAML 读,
故飞毛腿、直播升级等任意任务的任意 persona 都能复用同一个引擎。
"""
from __future__ import annotations

from ..models.persona import Persona

END = "END"


class StateMachine:
    """按 persona 的转移表推进状态。MVP 用确定性转移,保证可复现。"""

    def __init__(self, persona: Persona):
        self.persona = persona
        self.current = persona.initial_state

    def instruction(self) -> str:
        """当前状态下用户该做什么(将喂给 LLM 生成话术)。"""
        return self.persona.states.get(self.current, "")

    def advance(self) -> bool:
        """推进到下一状态。返回 True 表示已到达终止(END)。"""
        nxt = self.persona.transitions.get(self.current, END)
        self.current = nxt
        return nxt == END

    @property
    def finished(self) -> bool:
        return self.current == END
