"""能力目录的版本感知（审查 r5 留存项修复）：对话期只认在线版本的实体，
抽取防重名仍用全量历史；版本读不到时退回全量（行为与修复前一致）。"""

from __future__ import annotations

import json

import pytest

import syscraft.runtime.switcher as switcher
from syscraft.requirement.model_catalog import catalog


@pytest.fixture()
def models_dir(tmp_path):
    """两个版本、各有同名/异名实体：v7 有 book，v8 有 book（改名）+ reader。"""
    def write(name: str, version: str, entities: list[dict]):
        (tmp_path / name).write_text(json.dumps(
            {"model_version": version, "entities": entities}, ensure_ascii=False),
            encoding="utf-8")

    write("a.v7.json", "v7", [
        {"entity": "book", "display_name": "图书",
         "fields": [{"column": "id", "property": "id", "semantic": "主键", "type": "BIGINT"}]},
    ])
    write("b.v8.json", "v8", [
        {"entity": "book", "display_name": "书籍（新版）",
         "fields": [{"column": "id", "property": "id", "semantic": "主键", "type": "BIGINT"}]},
        {"entity": "reader", "display_name": "读者",
         "fields": [{"column": "id", "property": "id", "semantic": "主键", "type": "BIGINT"}]},
    ])
    return tmp_path


def test_online_catalog_only_running_version(models_dir, monkeypatch):
    monkeypatch.setattr("syscraft.config.MODELS_DIR", models_dir)
    monkeypatch.setattr(switcher, "current_version", lambda: "v8")
    entities = {e.entity: e for e in catalog(online_only=True).all()}
    assert set(entities) == {"book", "reader"}          # v7 的 book 不混入
    assert entities["book"].display_name == "书籍（新版）"


def test_online_catalog_falls_back_when_version_unknown(models_dir, monkeypatch):
    monkeypatch.setattr("syscraft.config.MODELS_DIR", models_dir)
    monkeypatch.setattr(switcher, "current_version", lambda: None)  # 产物停机
    names = {e.entity for e in catalog(online_only=True).all()}
    assert names == {"book", "reader"}                  # 全量退回（两版本同名取后者）


def test_full_catalog_still_covers_all_versions(models_dir, monkeypatch):
    """抽取防重名必须看全部历史版本——biz 库的旧表都还在。"""
    monkeypatch.setattr("syscraft.config.MODELS_DIR", models_dir)
    monkeypatch.setattr(switcher, "current_version", lambda: "v8")
    names = {e.entity for e in catalog().all()}
    assert "reader" in names                            # 全量视角不筛版本


def test_versioned_cache_keyed_by_version(models_dir, monkeypatch):
    monkeypatch.setattr("syscraft.config.MODELS_DIR", models_dir)
    monkeypatch.setattr(switcher, "current_version", lambda: "v7")
    assert {e.entity for e in catalog(online_only=True).all()} == {"book"}
    monkeypatch.setattr(switcher, "current_version", lambda: "v8")
    assert {e.entity for e in catalog(online_only=True).all()} == {"book", "reader"}
