"""共享 fixture。

catalog() 是进程级 lru_cache 单例：任何测试在 patch 过 MODELS_DIR 期间触发它
（生成流程 _extract 里必调），空/假目录就会被缓存住，污染后续全部测试——
单独跑通过、全量跑挂掉，是典型的缓存跨测试存活（实测踩过）。
每个测试前后各清一次，缓存永远不跨测试存活。"""

from __future__ import annotations

import pytest


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
