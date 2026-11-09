"""共享 fixture。

catalog() 是进程级 lru_cache 单例：任何测试在 patch 过 MODELS_DIR 期间触发它
（生成流程 _extract 里必调），空/假目录就会被缓存住，污染后续全部测试——
单独跑通过、全量跑挂掉，是典型的缓存跨测试存活（实测踩过）。
每个测试前后各清一次，缓存永远不跨测试存活。"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fresh_catalog_cache():
    from syscraft.requirement.model_catalog import catalog
    catalog.cache_clear()
    yield
    catalog.cache_clear()
