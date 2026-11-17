"""增量修改的实体别名解析（F8 自然口语「给订单加字段」误落业务模式的修复）。"""
import pytest

from syscraft.requirement.amend import parse_amend
from syscraft.requirement.model_catalog import EntityInfo, FieldInfo, ModelCatalog


def _cat(*entities):
    return ModelCatalog({
        e[0]: EntityInfo(entity=e[0], display_name=e[1],
                         fields=(FieldInfo(column="id", property="id",
                                           semantic="主键", type="BIGINT"),))
        for e in entities})


@pytest.fixture
def cat():
    """LLM 给实体起名「客户订单」的场景：用户口语只会说「订单」。"""
    return _cat(("cust_account", "客户"), ("cust_order", "客户订单"),
                ("cust_order_item", "订单明细"))


def test_exact_display_still_works(cat):
    r = parse_amend("给客户订单加一个备注字段", cat)
    assert r is not None and r.entity.entity == "cust_order"


def test_substring_alias_resolves_unique_suffix(cat):
    # 「订单」是「客户订单」的子串，但也是「订单明细」的子串——命中两个不猜
    assert parse_amend("给订单加一个发货日期字段", cat) is None


def test_unique_alias_hits(cat):
    # 只有一个显示名包含该词时才回退命中
    r = parse_amend("给项目加一个截止日期字段", _cat(("project", "项目任务跟踪")))
    assert r is not None and r.entity.entity == "project"


def test_single_char_word_never_aliases(cat):
    # 单字词太容易误伤（「给户加字段」），一律不回退
    assert parse_amend("给户加一个备注字段", _cat(("account", "客户账号"))) is None
