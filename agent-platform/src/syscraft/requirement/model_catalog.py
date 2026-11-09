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
        """按中文语义或列名在自然语言里找字段。用于解析「状态是已支付」这类条件。"""
        for field in self.fields:
            if field.semantic and field.semantic in text:
                return field
            if field.column in text:
                return field
        return None


class ModelCatalog:
    def __init__(self, entities: dict[str, EntityInfo]):
        self._entities = entities

    @classmethod
    def load(cls, models_dir: Path | None = None) -> "ModelCatalog":
        directory = models_dir or config.MODELS_DIR
        entities: dict[str, EntityInfo] = {}
        for path in sorted(directory.glob("*.json")):
            model = json.loads(path.read_text(encoding="utf-8"))
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


@lru_cache(maxsize=1)
def catalog() -> ModelCatalog:
    return ModelCatalog.load()
