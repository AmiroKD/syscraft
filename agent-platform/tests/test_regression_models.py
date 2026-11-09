"""模板回归矩阵（设计 7 节）：三个固定模型 → 生成 → 断言结构不变量。

编译级回归（生成→mvn 编译→断言）在 scripts/regression.sh——pytest 里跑三次
mvn 太慢，渲染级不变量每轮单测都跑，编译级改模板后手动/CI 跑脚本。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from syscraft import config
from syscraft.codegen.generator import generate

MODELS = Path(config.MODELS_DIR) / "regression"
VERSIONS = ["r1", "r2", "r3"]


@pytest.fixture(autouse=True)
def clean_versions():
    """每个用例前清掉对应版本目录：生成器拒绝覆盖是设计，测试各自从干净态开始
    （函数级清理——模块级会让最后的汇总用例撞上前面的生成产物）。"""
    for version in VERSIONS:
        shutil.rmtree(config.PROJECT_ROOT / "workspace/versions" / version, ignore_errors=True)
        for parent, pattern in (("scripts/ddl", f"{version}_*"), ("scripts/grants", f"{version}_*")):
            for leftover in (config.PROJECT_ROOT / "workspace" / parent).glob(pattern):
                leftover.unlink()
    yield


def _generate(version: str, filename: str) -> dict:
    return generate(MODELS / filename, config.PROJECT_ROOT)


def _version_dir(version: str) -> Path:
    return config.PROJECT_ROOT / "workspace/versions" / version


def _java(version: str, *parts: str) -> str:
    return (_version_dir(version) / "src/main/java/com/generated").joinpath(*parts).read_text(encoding="utf-8")


def test_r1_single_entity_with_state_machine():
    info = _generate("r1", "r1_single.json")
    assert info["files"] and info["ddl"] and info["grants"]

    service = _java("r1", "generated/service/RegTaskService.java")
    # 状态机：非法流转被拒，合法流转放行（模板的状态段；常量按字段名命名）
    assert "checkTransition" in service
    assert "STATUS_FLOW = List.of(\"TODO\", \"DOING\", \"DONE\")" in service
    # 无计算规则的实体不得调用不存在的方法（applyComputed 缺陷的回归锚点）
    assert "applyComputed" not in service

    controller = _java("r1", "generated/controller/RegTaskController.java")
    for endpoint in ("query", "aggregate", "insert", "update", "delete"):
        assert f'"/{endpoint}"' in controller


def test_r2_one_to_many_foreign_key():
    _generate("r2", "r2_one_to_many.json")
    # 主子表都有
    assert (_java("r2", "generated/entity/RegCustomer.java"))
    assert (_java("r2", "generated/entity/RegOrder.java"))
    # 外键以驼峰属性出现在子表
    order_entity = _java("r2", "generated/entity/RegOrder.java")
    assert "customerId" in order_entity
    # DDL 每实体一个文件，两张表且子表带外键列
    ddl_all = "\n".join(p.read_text(encoding="utf-8")
                        for p in (config.PROJECT_ROOT / "workspace/scripts/ddl").glob("r2_*.sql"))
    assert "CREATE TABLE IF NOT EXISTS reg_customer" in ddl_all
    assert "CREATE TABLE IF NOT EXISTS reg_order" in ddl_all
    assert "customer_id" in ddl_all
    # 掩码策略进入 DDL 注释链（phone 为 MASK：语义注释里仍然可读）
    assert "联系电话" in ddl_all


def test_r3_three_rule_types_together():
    _generate("r3", "r3_rules.json")
    service = _java("r3", "generated/service/RegPaymentService.java")
    # 三种规则段全部渲染
    assert "applyComputed" in service                      # 计算
    assert "数量必须大于 0" in service                       # 约束
    assert "checkTransition" in service                    # 状态机
    assert "STATUS_FLOW = List.of(\"PENDING\", \"PAID\", \"REFUNDED\")" in service


def test_all_versions_have_acceptance_spec_and_no_placeholder():
    for version, filename in (("r1", "r1_single.json"), ("r2", "r2_one_to_many.json"), ("r3", "r3_rules.json")):
        _generate(version, filename)
        # 无残留占位符（《代码生成器》5 节：StrictUndefined 之外的第二道防线）
        for java_file in (_version_dir(version) / "src/main/java").rglob("*.java"):
            text = java_file.read_text(encoding="utf-8")
            assert "{{" not in text and "{%" not in text, f"{java_file} 有残留模板占位符"
        # 验收清单随模型生成
        assert (config.PROJECT_ROOT / "templates/acceptance" / f"{version}.json").exists()
