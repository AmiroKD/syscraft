"""修复循环的模型适配层（设计 6.8）：JSON 输出实现，解析失败重试一次。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

from syscraft import config

SYSTEM_PROMPT = """你是 Java 编译错误修复器。根据给出的工程上下文，输出**一个 JSON 动作**（不要输出其它内容）：
{"tool":"read_file","path":"相对路径","start_line":1,"end_line":200}
{"tool":"edit_file","path":"相对路径","old_string":"原文","new_string":"替换后"}
{"tool":"grep","pattern":"正则"}
{"tool":"list_files","dir":"目录"}
{"tool":"run_build"}
一次只做一个动作。修完后必须 run_build 验证。"""

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


class RepairLLM:
    def __init__(self, llm_call=None):
        # 可注入的补全函数：messages -> (content, total_tokens)。测试用它替真 HTTP
        self._llm_call = llm_call or self._http_call

    def decide(self, context: str) -> tuple[dict, int]:
        """返回 (动作, 本次消耗 token)。输出坏 JSON 重试一次（设计 6.8），再坏抛异常。"""
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": context}]
        for attempt in (1, 2):
            content, tokens = self._llm_call(messages)
            action = _extract_json(content)
            if action is not None and isinstance(action.get("tool"), str):
                return action, tokens
            if attempt == 1:
                messages = messages + [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": "这不是合法动作 JSON。重新输出一个动作 JSON，不要其它内容。"}]
        raise ValueError(f"模型两次都给不出合法动作：{content[:200]}")

    def _http_call(self, messages: list[dict]) -> tuple[str, int]:
        response = httpx.post(
            f"{config.LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
            json={"model": config.LLM_MODEL, "messages": messages,
                  "max_tokens": 2048, "temperature": 0},
            timeout=config.LLM_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            raise RuntimeError(f"LLM HTTP {response.status_code}")
        body = response.json()
        return body["choices"][0]["message"]["content"] or "", \
            body.get("usage", {}).get("total_tokens", 0)


def _extract_json(content: str) -> dict | None:
    text = (content or "").strip()
    match = _FENCE.search(text)
    if match:
        text = match.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _unused(_path: Path) -> None:  # pragma: no cover
    pass
