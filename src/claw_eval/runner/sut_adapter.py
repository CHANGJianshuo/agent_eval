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
