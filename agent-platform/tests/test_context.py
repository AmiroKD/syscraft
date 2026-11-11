"""上下文裁剪：工具结果三档瘦身 + 历史轮次压缩（设计 2.1/2.3）。"""

from __future__ import annotations

from syscraft.conversation.context import llm_history, tool_result_for_llm


def make_rows(n: int) -> list[dict]:
    return [{"id": i, "order_no": f"SO-{i}", "amount": float(i)} for i in range(1, n + 1)]


def test_small_result_kept_in_full():
    trimmed = tool_result_for_llm(make_rows(5), 5)
    assert len(trimmed["rows"]) == 5 and trimmed["total"] == 5


def test_medium_result_shows_head_only():
    trimmed = tool_result_for_llm(make_rows(30), 30)
    assert len(trimmed["rowsShown"]) == 3 and "共 30 条" in trimmed["note"]


def test_large_result_prompts_aggregation():
    trimmed = tool_result_for_llm(make_rows(120), 120)
    assert "rows" not in trimmed and "columns" in trimmed
    assert "aggregate_records" in trimmed["note"]


def test_history_keeps_recent_rounds_and_compresses_older():
    messages: list[dict] = []
    for round_no in range(15):
        messages.append({"role": "user", "type": "text", "content": f"问题{round_no}"})
        messages.append({"role": "assistant", "type": "text",
                         "content": f"回答{round_no}" + "长" * 60})
    history = llm_history(messages)
    contents = [m["content"] for m in history]

    assert any("更早的对话摘要" in c for c in contents)          # 旧轮次被压缩
    assert any("问题14" in c for c in contents)                  # 最近轮次保留原文
    assert not any(c == "问题0" for c in contents)               # 第 0 轮已被压缩


def test_history_table_type_becomes_one_line():
    messages = [
        {"role": "user", "type": "text", "content": "查订单"},
        {"role": "assistant", "type": "table", "content": {"entity": "trade_order", "total": 128}},
    ]
    history = llm_history(messages)
    assert len(history) == 2
    assert "128" in history[1]["content"]  # 表格只留关键字段，不带行数据


def test_history_table_rows_never_enter_context():
    """表格落库带行数据（给界面还原），但给模型的历史只能是一句话。"""
    messages = [
        {"role": "user", "type": "text", "content": "查活动"},
        {"role": "assistant", "type": "table", "content": {
            "entity": "activity", "display": "活动", "total": 5,
            "rows": [{"name": "不该进上下文的行数据"}],
        }},
        {"role": "assistant", "type": "text", "content": "共 5 条。"},
    ]
    history = llm_history(messages)
    joined = "\n".join(m["content"] for m in history)
    assert "不该进上下文的行数据" not in joined
    assert "活动 共 5 条" in joined
