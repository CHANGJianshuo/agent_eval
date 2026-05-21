"""可复用状态机引擎 —— 控制用户模拟器的对话走向。

引擎本身不认识任何具体 state 名,states / transitions 全部从 persona YAML 读,
故飞毛腿、直播升级等任意任务的任意 persona 都能复用同一个引擎。

transitions 支持两种格式(向后兼容):
  确定性:transitions: {state_a: state_b}                — 必走 state_b
  概率型:transitions: {state_a: {state_b: 0.6, state_c: 0.4}}
         — 按权重抽样,用 seeded RNG 保证可复现
"""
from __future__ import annotations

import random
from typing import Union

from ..models.persona import Persona

END = "END"

# 一个 transition 值可能是 str 或 {state: weight} 字典
TransitionSpec = Union[str, dict[str, float]]


class StateMachine:
    """按 persona 的转移表推进状态。"""

    def __init__(self, persona: Persona, rng: random.Random | None = None):
        """rng 用于解析概率 transitions(seeded 可复现)。
        没传时使用 random 模块默认实例(不可复现 —— 仅适合 validator 这种不在乎可复现的用法)。
        """
        self.persona = persona
        self.current = persona.initial_state
        self._rng = rng

    def instruction(self) -> str:
        """当前状态下用户该做什么(将喂给 LLM 生成话术)。"""
        return self.persona.states.get(self.current, "")

    def advance(self) -> bool:
        """推进到下一状态。返回 True 表示已到达终止(END)。"""
        spec = self.persona.transitions.get(self.current, END)
        nxt = self._resolve(spec)
        self.current = nxt
        return nxt == END

    def _resolve(self, spec: TransitionSpec) -> str:
        """spec 是 str 直接走;是 dict 按权重抽。"""
        if isinstance(spec, str):
            return spec
        if isinstance(spec, dict):
            if not spec:
                return END
            keys = list(spec.keys())
            weights = [float(spec[k]) for k in keys]
            rng = self._rng if self._rng is not None else random
            # random.choices 在 stdlib;Random.choices 在 3.6+
            return rng.choices(keys, weights=weights, k=1)[0]
        # 不认识的类型 —— 视为终止
        return END

    @property
    def finished(self) -> bool:
        return self.current == END
