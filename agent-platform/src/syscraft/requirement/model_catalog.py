"""业务模型目录：把 models/*.json 读成「实体 / 字段 / 中文名」。

为什么平台要自己存一份：生成器已经从同一份模型渲染过代码，对话层要显示
「金额」而不是 amount，就得有同一套语义。等 /api/meta/snapshot 落地后，
这份目录改为从产物侧读取，本地只做缓存——现在读模型文件，避免两套真相。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from syscraft import config


@dataclass(frozen=True)
class FieldInfo:
    column: str
    property: str
    semantic: str
    type: str


@dataclass(frozen=True)
class EntityInfo:
    entity: str
    display_name: str
    fields: tuple[FieldInfo, ...]

    def field(self, column_or_property: str) -> FieldInfo | None:
        for field in self.fields:
            if column_or_property in (field.column, field.property):
                return field
        return None

    def match_field(self, text: str) -> FieldInfo | None:
        """按中文语义或列名在自然语言里找字段。用于解析「状态是已支付」这类条件。

        中文语义用子串（「订单号是SO-1」里切出来的就是「订单号」）；
        列名必须<b>整词相等</b>——子串匹配会让 user_id 误命中 id 列，这是实测踩过的坑。
        """
        for field in self.fields:
            if field.semantic and field.semantic in text:
                return field
        for field in self.fields:
            if field.column == text.strip():
                return field
        return None


class ModelCatalog:
    def __init__(self, entities: dict[str, EntityInfo]):
        self._entities = entities

    @classmethod
    def load(cls, models_dir: Path | None = None,
             online_version: str | None = None) -> "ModelCatalog":
        """online_version 给定时只加载该版本的模型（对话期用：产物里跑的
        只有这一套接口，历史版本的实体查询只会 404）；None 加载全部。"""
        directory = models_dir or config.MODELS_DIR
        entities: dict[str, EntityInfo] = {}
        for path in sorted(directory.glob("*.json")):
            model = json.loads(path.read_text(encoding="utf-8"))
            if online_version is not None and model.get("model_version") != online_version:
                continue
            for entity in model.get("entities", []):
                info = EntityInfo(
                    entity=entity["entity"],
                    display_name=entity["display_name"],
                    fields=tuple(
                        FieldInfo(
                            column=f["column"],
                            property=f["property"],
                            semantic=f.get("semantic", f["column"]),
                            type=f["type"],
                        )
                        for f in entity["fields"]
                    ),
                )
                entities[info.entity] = info
        return cls(entities)

    def all(self) -> list[EntityInfo]:
        return list(self._entities.values())

    def by_mention(self, text: str) -> EntityInfo | None:
        """在自然语言里认实体：中文名优先，其次表名。"""
        for info in self._entities.values():
            if info.display_name and info.display_name in text:
                return info
        for info in self._entities.values():
            if info.entity in text:
                return info
        return None

    def by_name(self, name: str) -> EntityInfo | None:
        return self._entities.get(name)


def reset_catalog_cache() -> None:
    """清两个视角的缓存。生成完成后必须调：新模型文件要进全量目录、
    工具白名单（online 视角换版本键后也一并清）要看见新实体。"""
    _full_catalog.cache_clear()
    _versioned_catalog.cache_clear()


@lru_cache(maxsize=1)
def _full_catalog() -> ModelCatalog:
    return ModelCatalog.load()


@lru_cache(maxsize=8)
def _versioned_catalog(version: str) -> ModelCatalog:
    # 键是版本号：切换/生成到新版本自然拿新目录，不会读到旧缓存
    return ModelCatalog.load(online_version=version)


def catalog(online_only: bool = False) -> ModelCatalog:
    """能力目录的两个视角（审查 r5 留存项：目录与在线版本漂移）：

    - online_only=False（全量历史）：抽取防重名用——biz 库的历史表都还在，
      新实体不能与任何旧版本撞名。
    - online_only=True（仅在线版本）：对话期实体解析/工具白名单用——产物里
      只有这一套接口，历史实体的查询只会 404。版本读不到（产物停机）时
      退回全量，行为与旿改前一致。"""
    if not online_only:
        return _full_catalog()
    from syscraft.runtime.switcher import current_version
    version = current_version()
    if not version:
        return _full_catalog()
    return _versioned_catalog(version)
