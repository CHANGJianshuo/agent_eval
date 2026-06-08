"""LiteLLM 封装 —— 统一各家模型接口 + 指数退避重试。

支持 OpenAI 兼容的第三方网关(DeepSeek / MiMo 等):
通过 configure() 设置 base_url 与 api_key,chat() 会自动以 openai/<model> 路由。
"""
from __future__ import annotations

import re
import time

_DEFAULT_API_BASE: str | None = None
_DEFAULT_API_KEY: str | None = None


def configure(api_base: str | None = None, api_key: str | None = None) -> None:
    """设置全局默认的 OpenAI 兼容网关地址与密钥(所有角色共用)。"""
    global _DEFAULT_API_BASE, _DEFAULT_API_KEY
    _DEFAULT_API_BASE = api_base
    _DEFAULT_API_KEY = api_key


def chat(model: str, messages: list[dict], temperature: float = 0.7,
         max_retries: int = 4, max_tokens: int = 4096,
         api_base: str | None = None, api_key: str | None = None,
         reasoning_effort: str | None = None,
         **kwargs) -> str:
    """调用对话模型,返回文本内容。失败时指数退避重试。

    reasoning_effort: low/medium/high —— 推理模型用,显式设置可大幅加快。
    litellm 在函数内惰性导入。
    """
    import litellm  # noqa: PLC0415

    api_base = api_base or _DEFAULT_API_BASE
    api_key = api_key or _DEFAULT_API_KEY

    call_model = model
    extra: dict = {}
    if api_base:
        extra["api_base"] = api_base
        extra["api_key"] = api_key
        # OpenAI 兼容网关:litellm 需要 openai/ 前缀来路由
        if "/" not in model:
            call_model = f"openai/{model}"
    if reasoning_effort:
        extra.setdefault("extra_body", {})["reasoning_effort"] = reasoning_effort

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = litellm.completion(
                model=call_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **extra,
                **kwargs,
            )
            msg = resp.choices[0].message
            text = msg.content or ""
            if not text:
                text = getattr(msg, "reasoning_content", "") or ""
            text = re.sub(r"<think>[\s\S]*?</think>", "", text)
            return text.strip()
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt == max_retries - 1:
                break
            time.sleep(2 ** attempt)
    raise RuntimeError(f"LLM 调用失败({model}): {last_err}")
