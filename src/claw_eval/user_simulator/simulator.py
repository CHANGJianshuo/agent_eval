"""用户模拟器 —— 状态机控走向 + LLM 生成话术 + 探针注入 + 按轮掷骰加噪。

system prompt 由 4 层合成:
  ① 性格(description/speaking_style)
  ② 噪音(per-turn 掷骰,命中才注入种类 instruction)
  ③ 当前状态指令(state instruction)
  ④ 通用规则
"""
from __future__ import annotations

import random

from ..models.persona import Persona
from ..models.trace import TraceMessage
from ..runner import llm_client
from .probes import probe_for_turn
from .state_machine import StateMachine

_SYSTEM = """\
你在模拟一位真实用户,正在接听一通电话。任务背景由「当前状态」给出。

## 你的性格
{description}

## 说话风格
{style}
{noise_block}
## 当前你的状态与该做的事
{instruction}

## 规则
1. 始终保持你的人设角色,用自然口语简短回复(1-2 句),像真打电话一样。
2. 只根据人设和当前状态回应,不要替对方说话,也不要主动替对方完成任务。
3. 不知道的信息就说「这个我不太清楚」之类的自然回复。
4. 不要暴露你是 AI,不要解释你在模拟。只输出你要说的那句话。
"""


class UserSimulator:
    """按状态机推进的模拟用户。"""

    def __init__(self, model: str, persona: Persona, temperature: float = 0.7,
                 reasoning_effort: str | None = None, seed: int = 0):
        self.model = model
        self.persona = persona
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        self.sm = StateMachine(persona)
        self.user_turn_index = 0
        self._rng = random.Random(seed)

    def next(self, messages: list[TraceMessage]) -> tuple[str, str, bool, str | None]:
        """生成用户的下一句。返回 (话术, 当前状态, 是否对话结束, 探针ID)。"""
        self.user_turn_index += 1
        state = self.sm.current

        probe = probe_for_turn(self.persona, self.user_turn_index)
        if probe is not None:
            text: str = probe.text
            probe_id: str | None = probe.id
        else:
            text = self._generate(messages)
            probe_id = None

        done = self.sm.advance()
        return text, state, done, probe_id

    # ------------------------------------------------------------------
    def _roll_noise(self) -> str:
        """按轮掷骰决定本轮是否加噪,以及加哪种。返回噪音 instruction(空串=不加)。"""
        if not self.persona.noise_kinds or self.persona.noise_rate <= 0:
            return ""
        if self._rng.random() >= self.persona.noise_rate:
            return ""
        kind = self._rng.choice(self.persona.noise_kinds)
        return kind.instruction

    def _generate(self, messages: list[TraceMessage]) -> str:
        noise_instruction = self._roll_noise()
        noise_block = ""
        if noise_instruction:
            noise_block = f"\n## 本轮噪音特点\n{noise_instruction}\n"
        system = _SYSTEM.format(
            description=self.persona.description,
            style=self.persona.speaking_style,
            noise_block=noise_block,
            instruction=self.sm.instruction(),
        )
        transcript = self._format(messages)
        user_msg = (
            f"以下是到目前为止的通话内容:\n\n{transcript or '(电话刚接通)'}\n\n"
            "请按你当前的状态,回复对方的最新一句话。只输出你要说的话。"
        )
        return llm_client.chat(
            self.model,
            [{"role": "system", "content": system},
             {"role": "user", "content": user_msg}],
            self.temperature,
            reasoning_effort=self.reasoning_effort,
        )

    @staticmethod
    def _format(messages: list[TraceMessage]) -> str:
        lines = []
        for m in messages:
            who = "对方" if m.role == "assistant" else "你"
            lines.append(f"{who}: {m.text}")
        return "\n".join(lines)
