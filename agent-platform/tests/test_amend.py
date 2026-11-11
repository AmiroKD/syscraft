"""增量修改（加字段）：解析、类型推断、模型合并、ALTER 语句与对话链路。

这一层的价值在于「不可逆操作要确定性」：字段名/类型/是否可空全部由规则决定，
模型不参与；测试只需要证明规则在常见输入上稳定，以及失败路径不静默。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import syscraft.agent.generate as gen_module
import syscraft.requirement.amend as amend_module
import syscraft.runtime.switcher as switcher_module
from syscraft.agent.generate import GenerationFlow
from syscraft.conversation.store import SessionStore
from syscraft.requirement.amend import alter_sql, append_field, build_field, parse_amend
from syscraft.requirement.model_catalog import EntityInfo, FieldInfo, ModelCatalog

MODEL = {
    "model_version": "v7",
    "entities": [{
        "entity": "activity", "class_name": "Activity", "display_name": "活动",
        "owner_field": None,
        "fields": [
            {"column": "id", "property": "id", "type": "BIGINT", "primary_key": True,
             "nullable": False, "unique": True, "semantic": "主键", "visibility": "PLAIN",
             "sensitive": False},
            {"column": "name", "property": "name", "type": "VARCHAR", "length": 128,
             "primary_key": False, "nullable": False, "unique": False,
             "semantic": "活动名称", "visibility": "PLAIN", "sensitive": False},
        ],
        "rules": {"computed": [], "constraints": [], "states": []},
    }],
    "grants": [{"role": "USER", "entity": "activity", "access": "READ"}],
}


def _catalog() -> ModelCatalog:
    return ModelCatalog({
        "activity": EntityInfo(
            entity="activity", display_name="活动",
            fields=(FieldInfo(column="id", property="id", semantic="主键", type="BIGINT"),
                    FieldInfo(column="name", property="name", semantic="活动名称", type="VARCHAR")),
        )
    })


# ---------------------------------------------------------------- 解析

def test_parse_amend_with_prefix():
    request = parse_amend("给活动加一个备注字段", _catalog())
    assert request is not None
    assert request.entity.entity == "activity"
    assert request.field_text == "备注"


def test_parse_amend_without_prefix_needs_explicit_field_word():
    assert parse_amend("活动加个备注字段", _catalog()) is not None
    # 没有「字段/列」结尾时不认，避免把「活动加个班」当成改表
    assert parse_amend("活动加个班", _catalog()) is None


def test_parse_amend_ignores_unknown_entity():
    """「给张三加一个账号」不是改表需求——实体不在目录里就必须回落业务模式。"""
    assert parse_amend("给张三加一个账号", _catalog()) is None


def test_parse_amend_matches_long_sentence():
    request = parse_amend("给活动管理系统加个备注字段", _catalog())
    assert request is not None and request.field_text == "备注"


# ---------------------------------------------------------------- 字段推断

def test_build_field_property_is_java_identifier():
    """property 从英文列名派生，不能从中文语义派生——否则生成 class 变量名「备注」。"""
    spec = build_field("备注", {"id", "name"})
    assert spec.column == "remark"
    assert spec.property == "remark"
    assert spec.property.isascii()


def test_build_field_dedups_column():
    assert build_field("备注", {"remark"}).column == "remark_b"
    # 「经办人」不在词表里，走 ext 兜底；被占用则字母后缀
    assert build_field("经办人", {"ext"}).column == "ext_b"


def test_column_property_roundtrip():
    """列名与 property 必须能互相映射：MyBatis 靠「驼峰转下划线」把 property
    还原成列名。数字后缀（remark_2 → remark2）转不回去，实测整个实体查询 500；
    所以后缀只能字母。"""
    import re

    def snake(name: str) -> str:
        return re.sub(r"([A-Z])", lambda m: "_" + m.group(1).lower(), name)

    for semantic, existing in (("备注", {"remark"}), ("联系人", {"ext"}),
                               ("报名截止时间", set()), ("金额", set())):
        spec = build_field(semantic, existing)
        assert snake(spec.property) == spec.column, (semantic, spec)


def test_infer_types_by_semantic():
    assert build_field("报名截止时间", set()).type == "DATETIME"
    assert build_field("金额", set()).type == "DECIMAL"
    assert build_field("名额", set()).type == "INT"
    assert build_field("是否通过", set()).type == "BOOLEAN"
    assert build_field("地址", set()).length == 255


def test_build_field_sanitizes_quotes():
    """用户说「加个'备注'」是自然表达，不是攻击：去字符而不是拒绝。"""
    spec = build_field("'备注'", set())
    assert "'" not in spec.semantic
    assert spec.column == "remark"


def test_new_field_is_nullable():
    """新列必须可空：存量行没有值，NOT NULL 在严格模式下会直接失败。"""
    assert build_field("备注", set()).as_model_field()["nullable"] is True


# ---------------------------------------------------------------- ALTER 与模型合并

def test_alter_sql_shape():
    field = build_field("备注", set()).as_model_field()
    sql = alter_sql("activity", field)
    assert sql.startswith("ALTER TABLE `activity` ADD COLUMN `remark` VARCHAR(255)")
    assert "NULL DEFAULT NULL" in sql
    assert "COMMENT '备注'" in sql


def test_alter_sql_escapes_quote():
    field = {"column": "remark", "semantic": "it's", "type": "VARCHAR", "length": 64}
    assert "it''s" in alter_sql("activity", field)


def test_append_field_rejects_duplicate_column():
    model = json.loads(json.dumps(MODEL))
    spec = build_field("备注", {"id", "name", "remark"})
    spec.column = "remark"
    model["entities"][0]["fields"].append({"column": "remark", "semantic": "旧备注"})
    assert "已经有" in (append_field(model, "activity", spec) or "")


def test_append_field_rejects_duplicate_semantic():
    model = json.loads(json.dumps(MODEL))
    model["entities"][0]["fields"].append({"column": "other_col", "semantic": "备注"})
    spec = build_field("备注", {"id", "name", "other_col"})
    assert "已经有" in (append_field(model, "activity", spec) or "")


def test_append_field_adds_once():
    model = json.loads(json.dumps(MODEL))
    spec = build_field("备注", {"id", "name"})
    assert append_field(model, "activity", spec) is None
    assert [f["column"] for f in model["entities"][0]["fields"]][-1] == "remark"


# ---------------------------------------------------------------- 对话链路

class FakePipeline:
    captured: dict = {}

    def __init__(self, **kwargs):
        FakePipeline.captured = kwargs

    def run(self, model_path):
        FakePipeline.captured["model_path"] = str(model_path)
        FakePipeline.captured["notify"]("③ 建表与迁移（biz）", True, "迁移加列 1")
        return SimpleNamespace(ok=True, failed_step=None, migrations=["activity.remark"],
                               steps=[SimpleNamespace(ok=True, detail="9/9 通过", name="x")])


async def _no_stop(self, emit):
    return None


@pytest.fixture()
def env(tmp_path, monkeypatch):
    models = tmp_path / "models"
    models.mkdir()
    monkeypatch.setattr(gen_module.config, "MODELS_DIR", models)
    monkeypatch.setattr(gen_module.config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("syscraft.requirement.model_catalog.catalog",
                        lambda online_only=False: _catalog())
    monkeypatch.setattr(amend_module, "load_online_model",
                        lambda version: (json.loads(json.dumps(MODEL)), None))
    monkeypatch.setattr(switcher_module, "current_version", lambda: "v7")
    monkeypatch.setattr(gen_module, "Pipeline", FakePipeline)
    monkeypatch.setattr(GenerationFlow, "_stop_old_product", _no_stop)
    return models


@pytest.mark.anyio
async def test_amend_plan_then_confirm_runs_migration(env, tmp_path):
    store = SessionStore()
    session = store.create(user_id=1)
    flow = GenerationFlow(client=None)

    planned = await flow.handle("给活动加一个备注字段", "tk", session,
                                lambda e: None, me={"roleCode": "SUPER_ADMIN"})
    assert "新增字段" in planned["reply"]
    assert session.get_slot("gen_mode") == "amend"
    assert session.get_slot("gen_state") == "awaiting_plan"
    plan_model = json.loads(Path(session.get_slot("gen_model_path")).read_text(encoding="utf-8"))
    assert any(f["column"] == "remark" for f in plan_model["entities"][0]["fields"])

    await flow.handle("确认生成", "tk", session, lambda e: None, me={"roleCode": "SUPER_ADMIN"})
    for _ in range(100):
        if session.get_slot("gen_state") is None:
            break
        await asyncio.sleep(0.05)

    assert FakePipeline.captured["allow_migrate"] is True
    assert session.get_slot("gen_state") is None
    assert session.get_slot("gen_mode") is None


@pytest.mark.anyio
async def test_amend_requires_admin(env, tmp_path):
    store = SessionStore()
    session = store.create(user_id=2)
    flow = GenerationFlow(client=None)
    result = await flow.handle("给活动加一个备注字段", "tk", session,
                               lambda e: None, me={"roleCode": "USER"})
    assert result is not None and "管理员权限" in result["reply"]
    assert session.get_slot("gen_state") is None


@pytest.mark.anyio
async def test_amend_cancel_clears_mode(env, tmp_path):
    store = SessionStore()
    session = store.create(user_id=1)
    flow = GenerationFlow(client=None)
    await flow.handle("给活动加一个备注字段", "tk", session,
                      lambda e: None, me={"roleCode": "SUPER_ADMIN"})
    result = await flow.handle("取消", "tk", session, lambda e: None,
                               me={"roleCode": "SUPER_ADMIN"})
    assert "取消" in result["reply"]
    assert session.get_slot("gen_mode") is None
    assert session.get_slot("gen_state") is None
