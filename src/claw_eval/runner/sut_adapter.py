"""SUT 适配器 —— 把被测对话模型包成「站长」。

只注入任务 Prompt,不加 RAG / 工具 / 多步逻辑 —— 确保评的是模型本身的
指令遵循能力,而不是我们写的脚手架。
"""
from __future__ import annotations

from ..models.trace import TraceMessage
from . import llm_client


class SUTAdapter:
    """被测对话模型(SUT)的薄适配器。"""

    def __init__(self, model: str, task_prompt: str, temperature: float = 0.7,
                 reasoning_effort: str | None = None):
        self.model = model
        self.system_prompt = task_prompt
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort

    def respond(self, messages: list[TraceMessage]) -> str:
        """根据对话历史生成站长的下一句话。"""
        chat_messages: list[dict] = [
            {"role": "system", "content": self.system_prompt}
        ]
        for m in messages:
            # user=骑手, assistant=站长(SUT 自己历史发言)
            chat_messages.append({"role": m.role, "content": m.text})
        return llm_client.chat(self.model, chat_messages, self.temperature,
                               reasoning_effort=self.reasoning_effort)

    def open_call(self) -> str:
        """外呼接通后由 SUT 主动说第一句话。"""
        return llm_client.chat(
            self.model,
            [
                {"role": "system", "content": self.system_prompt},
                {
                    "role": "user",
                    "content": (
                        "（系统事件：外呼电话刚刚接通。）请直接按照 Opening Line "
                        "说开场白并等待对方回应，只输出你要说的话。"
                    ),
                },
            ],
            self.temperature,
            reasoning_effort=self.reasoning_effort,
        )
