"""上下文裁剪（《会话与上下文策略》2 节）。

核心事实：给模型看的与给用户看的是两套策略。用户看到 10 行表格，
模型只该看到 3 行 + 总数——模型的职责是决定下一步查什么，不是复述数据，
给它全表只会把 token 花在抄写上。
"""

from __future__ import annotations

from typing import Any

# 保留原文的轮数（一轮 = 一次用户输入 + 一次助手回复）
MAX_ROUNDS_VERBATIM = 12

# 工具结果进上下文的三档阈值（设计 2.3）
_FULL_ROWS = 5
_PARTIAL_ROWS = 50


def tool_result_for_llm(rows: list[dict], total: int) -> dict[str, Any]:
    """查询结果瘦身：≤5 行全字段；6–50 行表头+前 3 行+总数；>50 行表头+总数+聚合提示。"""
    count = len(rows)
    if count <= _FULL_ROWS:
        return {"total": total, "rows": rows}
    if count <= _PARTIAL_ROWS:
        return {"total": total, "rowsShown": rows[:3], "note": f"共 {total} 条，仅展示前 3 行"}
    keys = sorted({k for row in rows[:5] for k in row}) if rows else []
    return {"total": total, "columns": keys,
            "note": f"共 {total} 条。数据量大，可按字段分组统计（aggregate_records）获取概貌"}


def llm_history(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """把会话消息压成 LLM 可用的历史。

    最近 12 轮保留原文（表格只留「查到 N 条」一行——完整数据模型看过了，
    复述它只会撑爆窗口）；更早的轮次压成一行摘要（设计 2.1/2.2）。
    """
    # 只取对话主线，工具调用细节不进历史
    turns = [m for m in messages if m["role"] in ("user", "assistant")]
    # 一轮 = user + assistant，这里按 user 消息切分
    rounds: list[list[dict]] = []
    for message in turns:
        if message["role"] == "user" or not rounds:
            rounds.append([message])
        else:
            rounds[-1].append(message)

    recent = rounds[-MAX_ROUNDS_VERBATIM:]
    older = rounds[:-MAX_ROUNDS_VERBATIM] if len(rounds) > MAX_ROUNDS_VERBATIM else []

    result: list[dict[str, str]] = []
    if older:
        summary = "；".join(
            f"{m['role'] == 'user' and '用户' or '助手'}：{_brief(m['content'])}"
            for rnd in older for m in rnd)
        result.append({"role": "assistant",
                       "content": f"（更早的对话摘要：{summary[-400:]}）"})
    for rnd in recent:
        for message in rnd:
            role = "user" if message["role"] == "user" else "assistant"
            content = message["content"]
            if message.get("type") == "table":
                content = f"{_brief(content) if isinstance(content, str) else content}"
            result.append({"role": role, "content": _as_text(content)})
    return result


def _brief(content: Any) -> str:
    text = content if isinstance(content, str) else json_dumps(content)
    return text[:40].replace("\n", " ")


def _as_text(content: Any) -> str:
    return content if isinstance(content, str) else json_dumps(content)


def json_dumps(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False)
