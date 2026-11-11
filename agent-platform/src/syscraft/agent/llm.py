"""LLM 工具调用适配：OpenAI 兼容 chat/completions + 原生 tools。

deepseek-flash 实测支持 function calling（finish_reason=tool_calls）。
解析失败或网络异常由上层（图的 agent 节点）兜底，这里只负责一次诚实的调用。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from syscraft import config


class LLMError(Exception):
    """LLM 调用失败（网络/HTTP/响应形态不合法）。"""


async def chat_with_tools(messages: list[dict], tools: list[dict]) -> dict[str, Any]:
    """一轮工具感知的补全。

    返回 {"content": str|None, "tool_calls": [{"id", "name", "arguments": dict}]}。
    参数 JSON 解析失败的调用会带 ``arguments_error`` 返回，由图节点翻译成
    工具结果回灌模型——静默变成空对象会让模型以为「实体名是空」，越改越错。
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
    response = await _post(payload)

    try:
        message = response.json()["choices"][0]["message"]
    except (ValueError, KeyError, IndexError) as error:
        raise LLMError("LLM 响应形态不合法") from error

    content = message.get("content")
    if isinstance(content, list):
        # 部分兼容网关把 content 返回成分片数组
        content = "".join(str(part.get("text", "")) for part in content
                          if isinstance(part, dict))
    tool_calls = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        arguments, arguments_error = _parse_arguments(function.get("arguments"))
        tool_calls.append({"id": call.get("id") or f"call_{len(tool_calls)}",
                           "name": function.get("name", ""),
                           "arguments": arguments,
                           "arguments_error": arguments_error})
    return {"content": content, "tool_calls": tool_calls}


async def _post(payload: dict) -> httpx.Response:
    """一次补全请求。网络层抖动重试一次：对话对延迟敏感，但不能因为一次
    keep-alive 断连就让整轮失败——第二次仍失败才如实上抛。"""
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=config.LLM_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"{config.LLM_BASE_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                    json=payload,
                )
            if response.status_code != 200:
                raise LLMError(f"LLM HTTP {response.status_code}: {response.text[:200]}")
            return response
        except httpx.TransportError as error:
            last_error = error
            if attempt == 0:
                await asyncio.sleep(0.4)
    raise LLMError(
        f"无法连接模型服务（{_describe_error(last_error)}）"
        "，请检查「设置 → 模型服务」里的 Base URL、网络或服务是否在线"
    )


def _describe_error(error: Exception | None) -> str:
    """httpx 的 ConnectError 经常 str() 为空，直接拼进去就是一句「LLM 网络错误：」。"""
    if error is None:
        return "未知网络错误"
    detail = str(error).strip()
    name = type(error).__name__
    return f"{name}: {detail}" if detail else name


def _parse_arguments(raw: Any) -> tuple[dict, str | None]:
    """解析工具参数。合法 JSON 对象 → (dict, None)；否则 ({}, 错误说明)。"""
    if raw is None or raw == "":
        return {}, None
    if isinstance(raw, dict):
        return raw, None
    if not isinstance(raw, str):
        return {}, f"工具参数类型不合法：{type(raw).__name__}"
    try:
        parsed = json.loads(raw)
    except ValueError as error:
        return {}, f"工具参数不是合法 JSON：{error}"
    if not isinstance(parsed, dict):
        return {}, f"工具参数必须是 JSON 对象，收到 {type(parsed).__name__}"
    return parsed, None
