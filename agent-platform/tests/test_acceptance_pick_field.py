"""验收器更新字段选择的回归（S5/S6 误判 FAIL 的根因修复）。"""
from syscraft.runtime.acceptance import pick_updatable_field


def test_skips_date_like_and_picks_text():
    # 维修记录样本：repair_date 排第一但不能选，选中 fault_desc
    sample = {"repair_date": "2026-11-09", "fault_desc": "TfaultDe", "cost": 100}
    assert pick_updatable_field(sample) == "fault_desc"


def test_datetime_style_also_skipped():
    sample = {"created_at": "2026-11-09T10:00:00", "name": "Tname"}
    assert pick_updatable_field(sample) == "name"


def test_pure_date_table_returns_none():
    sample = {"duty_date": "2026-11-09", "shift": 1}
    assert pick_updatable_field(sample) is None


def test_text_first_still_works():
    sample = {"device_name": "TdeviceNa", "purchase_date": "2026-11-09"}
    assert pick_updatable_field(sample) == "device_name"


# ---------------- 生成器侧 updatableField 标注（S6 短列溢出的根因修复） ----------------

from syscraft.codegen.generator import updatable_field


def test_prefers_long_varchar_over_short():
    entity = {"fields": [
        {"column": "shift", "property": "shift", "type": "VARCHAR", "length": 8},
        {"column": "remark", "property": "remark", "type": "VARCHAR", "length": 191},
    ]}
    # shift 样本 "Tshift"(6)+4=10 > 8 不安全；remark "Tremark"(7)+4=11 <= 191
    assert updatable_field(entity) == "remark"


def test_text_beats_long_varchar():
    entity = {"fields": [
        {"column": "remark", "property": "remark", "type": "VARCHAR", "length": 191},
        {"column": "desc", "property": "desc", "type": "TEXT"},
    ]}
    assert updatable_field(entity) == "desc"


def test_all_short_returns_none():
    entity = {"fields": [
        {"column": "shift", "property": "shift", "type": "VARCHAR", "length": 8},
        {"column": "duty_date", "property": "dutyDate", "type": "DATE"},
    ]}
    assert updatable_field(entity) is None


# ---------------- 样本构造：计算目标列排除 + DECIMAL 不按列名置空（S7/S8 根因） ----------------

from syscraft.codegen.generator import _crud_sample, sample_value


def test_decimal_gets_value_regardless_of_column_name():
    # S7 expense_item.amount / S8 payment_record.amount：普通必填列，不能按列名置 None
    f = {"column": "amount", "property": "amount", "type": "DECIMAL"}
    assert sample_value(f) == 100


def test_computed_target_excluded_from_sample():
    entity = {"fields": [
        {"column": "id", "property": "id", "type": "BIGINT", "primary_key": True},
        {"column": "quantity", "property": "quantity", "type": "INT"},
        {"column": "unit_price", "property": "unitPrice", "type": "DECIMAL"},
        {"column": "amount", "property": "amount", "type": "DECIMAL", "nullable": False},
    ], "rules": {"computed": [{"target": "amount", "left": "quantity",
                               "right": "unit_price", "op": "multiply"}]}}
    sample = _crud_sample(entity)
    assert "amount" not in sample          # 服务端重算，传值无意义
    assert sample["quantity"] == 2
    assert sample["unit_price"] == 100


def test_plain_amount_stays_in_sample():
    entity = {"fields": [
        {"column": "amount", "property": "amount", "type": "DECIMAL", "nullable": False},
    ], "rules": {"computed": []}}
    assert _crud_sample(entity) == {"amount": 100}
