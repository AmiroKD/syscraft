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

# 单条历史消息进上下文的硬上限。表格消息现在会把行数据一起落库（重开时才能
# 还原表格），但给模型看的历史只能是一句话——不然一次 200 行的查询就把窗口塞满。
MAX_CONTENT_CHARS = 1200


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
            content = _history_text(message)
            if content:
                result.append({"role": role, "content": content})
    return result


def _history_text(message: dict[str, Any]) -> str:
    """单条消息压成给模型看的一句话。

    结构化消息（表格/计划/确认）只留「发生了什么 + 关键数字」，行数据一律不进
    ——模型不需要复述数据，它只需要知道上一轮查过什么、要不要接着查。
    """
    type_ = message.get("type")
    content = message.get("content")
    if type_ == "table" and isinstance(content, dict):
        display = content.get("display") or content.get("entity") or "数据"
        total = content.get("total", content.get("displayed", 0))
        return f"（查询结果：{display} 共 {total} 条，明细已在界面上展示）"
    if type_ == "confirm" and isinstance(content, dict):
        return f"（系统已给出删除预告：{str(content.get('summary', ''))[:120]}）"
    if type_ == "plan" and isinstance(content, dict):
        return f"（生成计划：{str(content.get('summary', ''))[:200]}）"
    if type_ == "ask" and isinstance(content, dict):
        # 追问卡在界面上是可点选的表单/选项，模型只需要知道「上一轮问了什么」
        return f"（系统向用户追问：{str(content.get('text', ''))[:200]}）"
    if type_ == "error" and isinstance(content, dict):
        return f"（处理失败：{str(content.get('message', ''))[:200]}）"
    if type_ == "notice":
        # 暂停说明是给用户看的界面提示，不是对话内容：喂给模型会变成
        # 「助手说自己被暂停了」，下一轮容易被它当作上下文接着演
        return ""
    if content is None:
        return ""
    return _as_text(content)[:MAX_CONTENT_CHARS]


def _brief(content: Any) -> str:
    text = content if isinstance(content, str) else json_dumps(content)
    return text[:40].replace("\n", " ")


def _as_text(content: Any) -> str:
    return content if isinstance(content, str) else json_dumps(content)


def json_dumps(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False)
