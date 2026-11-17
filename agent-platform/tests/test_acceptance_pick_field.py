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
