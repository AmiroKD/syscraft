"""LLM 工具调用适配：OpenAI 兼容 chat/completions + 原生 tools。

deepseek-flash 实测支持 function calling（finish_reason=tool_calls）。
解析失败或网络异常由上层（图的 agent 节点）兜底，这里只负责一次诚实的调用。
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from syscraft import config


class LLMError(Exception):
    """LLM 调用失败（网络/HTTP/响应形态不合法）。"""


async def chat_with_tools(messages: list[dict], tools: list[dict]) -> dict[str, Any]:
    """一轮工具感知的补全。

    返回 {"content": str|None, "tool_calls": [{"id", "name", "arguments": dict}]}。
    """
    payload = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        # 工具循环每轮都要重新思考，1024 会把带推理的输出截断（实测）
        "max_tokens": max(config.LLM_MAX_TOKENS, 2048),
        "temperature": 0,
    }
    try:
        async with httpx.AsyncClient(timeout=config.LLM_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{config.LLM_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                json=payload,
            )
    except httpx.HTTPError as error:
        raise LLMError(f"LLM 网络错误：{error}") from error

    if response.status_code != 200:
        raise LLMError(f"LLM HTTP {response.status_code}: {response.text[:200]}")

    try:
        message = response.json()["choices"][0]["message"]
    except (ValueError, KeyError, IndexError) as error:
        raise LLMError("LLM 响应形态不合法") from error

    tool_calls = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except ValueError:
            arguments = {}
        tool_calls.append({"id": call.get("id") or f"call_{len(tool_calls)}",
                           "name": function.get("name", ""),
                           "arguments": arguments if isinstance(arguments, dict) else {}})
    return {"content": message.get("content"), "tool_calls": tool_calls}
