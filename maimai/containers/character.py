"""角色容器。

移植自 Lionheart ``src/containers/Character.ts``。

与上游的一处差异：Lionheart 的字段白名单写成了
``['point', 'level', ' awakening', 'useCount']``，其中 ``' awakening'``
**多了一个前导空格**，导致修改 ``awakening`` 永远不会被登记为待上传变更。
本实现修正为 ``"awakening"``。
"""

from __future__ import annotations

from typing import Any, Iterable, Iterator, Mapping

__all__ = ["Character", "CharacterSet"]

_SLOTS = ("point", "level", "awakening", "_on_change")

#: 改动这些字段才登记变更
_WRITABLE_FIELDS = frozenset({"point", "level", "awakening", "useCount"})


class Character:
    """单个角色的养成状态。"""

    __slots__ = _SLOTS

    def __init__(self, point: int = 0, level: int = 0, awakening: int = 0) -> None:
        object.__setattr__(self, "point", point)
        object.__setattr__(self, "level", level)
        object.__setattr__(self, "awakening", awakening)
        object.__setattr__(self, "_on_change", None)

    def __setattr__(self, name: str, value: Any) -> None:
        object.__setattr__(self, name, value)
        if name in _WRITABLE_FIELDS:
            callback = getattr(self, "_on_change", None)
            if callback is not None:
                callback()

    def __repr__(self) -> str:
        return (
            f"Character(point={self.point}, level={self.level}, "
            f"awakening={self.awakening})"
        )


class _ModifiedCharacter:
    __slots__ = ("character_id", "character", "is_new")

    def __init__(self, character_id: int, character: Character, is_new: bool):
        self.character_id = character_id
        self.character = character
        self.is_new = is_new

    def to_export(self) -> dict[str, Any]:
        return {
            "value": {
                "characterId": self.character_id,
                "point": self.character.point,
                "level": self.character.level,
                "awakening": self.character.awakening,
                "useCount": 0,
            },
            "isNew": self.is_new,
        }


class CharacterSet:
    """一批角色，按 ``characterId`` 索引。"""

    def __init__(self, data: Iterable[Mapping[str, Any]] = ()) -> None:
        self._character: dict[int, Character] = {}
        self._modified: dict[int, _ModifiedCharacter] = {}

        for detail in data:
            character_id = int(detail["characterId"])
            self._character[character_id] = Character(
                point=int(detail.get("point") or 0),
                level=int(detail.get("level") or 0),
                awakening=int(detail.get("awakening") or 0),
            )

    # ---- 读取 -----------------------------------------------------------
    def get(self, character_id: int) -> Character | None:
        """取得角色。未加载的角色返回 ``None``（注意与 ScoreSet 不同）。"""
        character = self._character.get(character_id)
        if character is None:
            return None
        character._on_change = lambda: self._track(character_id, character)
        return character

    def keys(self):
        return self._character.keys()

    def values(self) -> Iterator[Character]:
        return (self.get(cid) for cid in self._character)

    def items(self):
        return ((cid, self.get(cid)) for cid in self._character)

    def __iter__(self) -> Iterator[int]:
        return iter(self._character)

    def __len__(self) -> int:
        return len(self._character)

    def __contains__(self, character_id: object) -> bool:
        return character_id in self._character

    # ---- 写入 -----------------------------------------------------------
    def set(self, character_id: int, character: Mapping[str, Any]) -> None:
        """写入角色。已存在的就地更新（不标 isNew），新角色标 ``isNew=True``。"""
        original = self._character.get(character_id)
        if original is not None:
            original.point = int(character.get("point") or 0)
            original.level = int(character.get("level") or 0)
            original.awakening = int(character.get("awakening") or 0)
            self._track(character_id, original, is_new=False)
            return

        created = Character(
            point=int(character.get("point") or 0),
            level=int(character.get("level") or 0),
            awakening=int(character.get("awakening") or 0),
        )
        self._character[character_id] = created
        self._track(character_id, created, is_new=True)

    # ---- 导出 -----------------------------------------------------------
    def export(self) -> list[dict[str, Any]]:
        return [item.to_export() for item in self._modified.values()]

    @property
    def modified_count(self) -> int:
        return len(self._modified)

    # ---- 内部 -----------------------------------------------------------
    def _track(self, character_id: int, character: Character, is_new: bool = False) -> None:
        existing = self._modified.get(character_id)
        if existing is not None:
            existing.character = character
            existing.is_new = existing.is_new or is_new
        else:
            self._modified[character_id] = _ModifiedCharacter(
                character_id, character, is_new
            )
