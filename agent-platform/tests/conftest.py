"""共享 fixture。

catalog() 是进程级 lru_cache 单例：任何测试在 patch 过 MODELS_DIR 期间触发它
（生成流程 _extract 里必调），空/假目录就会被缓存住，污染后续全部测试——
单独跑通过、全量跑挂掉，是典型的缓存跨测试存活（实测踩过）。
每个测试前后各清一次，缓存永远不跨测试存活。"""

from __future__ import annotations

import os

import pytest

# 必须在任何 syscraft 模块导入之前定死：config 在导入时读环境变量。
# 会话现在落在 MySQL，测试绝不能指向真实的 chat 库——那会把用户的会话清空。
os.environ["CHAT_DB_NAME"] = "chat_test"


@pytest.fixture(autouse=True)
def _clean_chat_schema():
    """每个用例前清空会话表。

    三道闸：库名必须以 _test 结尾、只 TRUNCATE 三张会话表、连不上库时静默跳过——
    纯单测（不碰会话）不该因为本机没起 MySQL 就全红，真正用到库的用例会自己报错。
    """
    from syscraft import config
    from syscraft.conversation.store import SessionStore

    if not config.CHAT_DB_NAME.endswith("_test"):
        raise RuntimeError(f"测试只允许清空 _test 结尾的库，当前是 {config.CHAT_DB_NAME!r}")
    try:
        store = SessionStore()
        with store._cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS = 0")
            for table in ("chat_message", "chat_slot", "chat_session"):
                cur.execute(f"TRUNCATE TABLE {table}")
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    except Exception as error:  # noqa: BLE001 库不可达不在这里拦，交给具体用例暴露
        # 但必须留一句人话：否则表还没建时，前面的用例会以莫名的业务错误失败，
        # 现场排查半天才发现是忘了跑 init_chat_db.sh
        print(f"\n[conftest] chat_test 库不可用（{error.__class__.__name__}）："
              "纯单测继续，碰库的用例会失败。 remediation：source ~/.adaptive/db.env 后 "
              "运行 ./scripts/init_chat_db.sh", flush=True)
    yield


@pytest.fixture(autouse=True)
def _fresh_catalog_cache():
    from syscraft.requirement.model_catalog import reset_catalog_cache
    reset_catalog_cache()
    yield
    reset_catalog_cache()


@pytest.fixture(autouse=True)
def _offline_artifact(monkeypatch):
    """单元测试不依赖产物在线状态：catalog(online_only=True) 的版本探针
    一律当「读不到」（退回全量目录）。否则带 db.env 跑测试时探针会拿到
    真实在线版本，同一个用例在 v7 在线时红、离线时绿——环境决定结果。
    要测版本过滤的用例自己 stub syscraft.runtime.switcher.current_version。"""
    import syscraft.runtime.switcher as switcher
    monkeypatch.setattr(switcher, "current_version", lambda: None)
