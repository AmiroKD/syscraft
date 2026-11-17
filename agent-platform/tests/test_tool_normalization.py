"""工具参数归一化：模型的「另一种写法」不该变成工具失败。

这批用例对应真实故障：模型传中文实体名、扁平 filter、小写 like、带 % 的值、
小写聚合函数，旧实现要么直接报错、要么查成 0 条后反复重试到轮次耗尽。
"""

from __future__ import annotations

import json

import pytest

from syscraft.agent.tools import ToolBox, ToolInputError
from syscraft.requirement.model_catalog import EntityInfo, FieldInfo, ModelCatalog


def make_catalog() -> ModelCatalog:
    return ModelCatalog({
        "activity": EntityInfo(
            entity="activity", display_name="活动",
            fields=(
                FieldInfo("id", "id", "主键", "BIGINT"),
                FieldInfo("name", "name", "活动名称", "VARCHAR"),
                FieldInfo("capacity", "capacity", "名额上限", "INT"),
                FieldInfo("status", "status", "报名状态", "VARCHAR"),
            ),
        )
    })


class FakeClient:
    def __init__(self):
        self.calls: list[tuple] = []

    async def query(self, entity, request, token):
        self.calls.append(("query", entity, request))
        rows = [{"id": 1, "name": "读书会", "capacity": 60, "status": "OPEN"}]
        return {"rows": rows, "total": len(rows), "page": 1, "size": 20, "truncated": False}

    async def aggregate(self, entity, request, token):
        self.calls.append(("aggregate", entity, request))
        return [{"status": "OPEN", "cnt": 1}]

    async def insert(self, entity, row, token):
        self.calls.append(("insert", entity, row))
        return 1

    async def update(self, entity, record, token):
        self.calls.append(("update", entity, record))
        return 1

    async def preview_delete(self, entity, ids, token):
        self.calls.append(("preview", entity, ids))
        return {"token": "tok", "summary": f"将删除 {len(ids)} 条"}


def box() -> tuple[ToolBox, FakeClient]:
    client = FakeClient()
    return ToolBox(client, make_catalog(), "tk"), client


@pytest.mark.anyio
async def test_display_name_flat_filter_and_like_percent():
    toolbox, client = box()
    await toolbox.call("query_records", {
        "entity": "活动",
        "filter": {"field": "活动名称", "operator": "like", "value": "%读书%"},
    })
    name, entity, request = client.calls[0]
    assert name == "query" and entity == "activity"
    assert request["filter"] == {"op": "AND", "conditions": [
        {"field": "name", "operator": "LIKE", "value": "读书"}]}


@pytest.mark.anyio
async def test_filter_list_and_symbol_operator():
    toolbox, client = box()
    await toolbox.call("query_records", {
        "entity": "activity",
        "filter": [{"field": "名额上限", "operator": ">=", "value": 50}],
    })
    request = client.calls[0][2]
    assert request["filter"]["conditions"][0] == {
        "field": "capacity", "operator": "GE", "value": 50}


@pytest.mark.anyio
async def test_filter_json_string_is_parsed():
    toolbox, client = box()
    await toolbox.call("query_records", {
        "entity": "activity",
        "filter": json.dumps({"conditions": [
            {"field": "status", "operator": "EQ", "value": "OPEN"}]}),
    })
    request = client.calls[0][2]
    assert request["filter"]["conditions"][0]["field"] == "status"


@pytest.mark.anyio
async def test_bad_page_type_is_tool_input_error():
    toolbox, _ = box()
    with pytest.raises(ToolInputError):
        await toolbox.call("query_records", {"entity": "activity", "size": "很多"})


@pytest.mark.anyio
async def test_aggregate_metrics_normalized():
    toolbox, client = box()
    await toolbox.call("aggregate_records", {
        "entity": "活动",
        "groupBy": ["报名状态"],
        "metrics": [{"func": "sum", "field": "名额上限", "alias": "名额合计"}],
    })
    request = client.calls[0][2]
    assert request["groupBy"] == ["status"]
    assert request["metrics"] == [{"func": "SUM", "field": "capacity", "alias": "名额合计"}]


@pytest.mark.anyio
async def test_aggregate_unknown_func_rejected():
    toolbox, _ = box()
    with pytest.raises(ToolInputError):
        await toolbox.call("aggregate_records", {
            "entity": "activity",
            "metrics": [{"func": "MEDIAN", "field": "capacity"}],
        })


@pytest.mark.anyio
async def test_delete_ids_from_comma_string():
    toolbox, client = box()
    result = await toolbox.call("delete_records", {"entity": "活动", "ids": "3, 5"})
    assert result["ids"] == [3, 5]
    assert client.calls[0] == ("preview", "activity", [3, 5])


@pytest.mark.anyio
async def test_insert_row_semantic_field_normalized():
    toolbox, client = box()
    await toolbox.call("insert_records", {
        "entity": "活动",
        "row": {"活动名称": "新读书会", "名额上限": 30},
    })
    assert client.calls[0] == ("insert", "activity", {"name": "新读书会", "capacity": 30})


@pytest.mark.anyio
async def test_unknown_field_lists_available_columns():
    toolbox, _ = box()
    with pytest.raises(ToolInputError) as caught:
        await toolbox.call("query_records", {
            "entity": "activity",
            "filter": {"field": "报名人数", "operator": "GT", "value": 0},
        })
    assert "capacity" in str(caught.value)


@pytest.mark.anyio
async def test_order_by_string_and_dict_normalized():
    toolbox, client = box()
    await toolbox.call("query_records", {
        "entity": "活动",
        "orderBy": ["名额上限 desc", {"field": "报名状态", "direction": "asc"}],
    })
    request = client.calls[0][2]
    assert request["orderBy"] == [
        {"field": "capacity", "direction": "DESC"},
        {"field": "status", "direction": "ASC"},
    ]


# ---------------- update 确认流（协议 4.7：修改与删除同为高危） ----------------

class FakeConfirmClient(FakeClient):
    """在 FakeClient 上补预告/确认执行两个方法，记录调用序列。"""

    def __init__(self):
        super().__init__()
        self.confirmed_rows = 0

    async def preview_update(self, entity, ids, values_hash, token):
        self.calls.append(("preview_update", entity, ids, values_hash[:8]))
        return {"token": "tok-u", "summary": f"将修改 {len(ids)} 条"}

    async def update_confirmed(self, entity, ids, values, values_hash, confirm_token, token):
        self.calls.append(("update_confirmed", entity, ids, dict(values)))
        return len(ids)


class FakeSession:
    def __init__(self):
        self.slots: dict = {}

    def get_slot(self, key):
        return self.slots.get(key)

    def set_slot(self, key, value):
        self.slots[key] = value


@pytest.mark.anyio
async def test_update_goes_through_confirmation():
    """update 不再直通：先预告、槽位挂起，模型拿不到执行权。"""
    toolbox, client = box()
    client.__class__ = FakeConfirmClient
    session = FakeSession()
    result = await toolbox.call("update_records",
                                {"entity": "活动", "id": 3, "values": {"名额上限": 88}},
                                session)
    assert result["status"] == "need_confirmation"
    assert result["summary"].startswith("将修改")
    # 预告已发（带 valuesHash），执行还没发生
    assert client.calls[0][0] == "preview_update"
    assert not any(call[0] == "update_confirmed" for call in client.calls)
    # 槽位挂好了：op/令牌/值齐备，编排层核销时用
    pending = session.get_slot("pending_confirm")
    assert pending["op"] == "update"
    assert pending["ids"] == [3]
    assert pending["values"] == {"capacity": 88}
    assert len(pending["valuesHash"]) == 64


@pytest.mark.anyio
async def test_update_values_cannot_touch_id():
    """values 里混进 id 也不允许改主键：目标行由 ids/filter 决定。"""
    toolbox, client = box()
    client.__class__ = FakeConfirmClient
    session = FakeSession()
    result = await toolbox.call("update_records",
                                {"entity": "活动", "id": 3,
                                 "values": {"id": 99, "名额上限": 88}}, session)
    pending = session.get_slot("pending_confirm")
    assert "id" not in pending["values"]
    assert result["status"] == "need_confirmation"


@pytest.mark.anyio
async def test_update_without_target_is_input_error():
    """既没有 id 也没有 filter：参数错误，不进预告。"""
    import pytest as _pytest
    from syscraft.agent.tools import ToolInputError
    toolbox, client = box()
    client.__class__ = FakeConfirmClient
    with _pytest.raises(ToolInputError):
        await toolbox.call("update_records", {"entity": "活动", "values": {"名额上限": 88}})
