"""道具容器。

移植自 Lionheart ``src/containers/Item.ts``。

含礼物（Present）的打包/解包逻辑：礼物 ID 是
``itemKind * 1_000_000 + itemId`` 的复合值，对应游戏内
``ConstParameter.PresentKindConvert = 1000000``。
"""

from __future__ import annotations

from typing import Any, Iterable, Iterator, Mapping

from ..typings.base import UserItemKind

__all__ = ["ItemSet", "unpack_present", "pack_present", "PRESENT_KIND_CONVERT"]

#: 礼物 ID 的进位基数，取自游戏 ``ConstParameter.PresentKindConvert``
PRESENT_KIND_CONVERT = 1_000_000


def unpack_present(present_id: int) -> tuple[int, int]:
    """把复合礼物 ID 拆成 ``(itemKind, itemId)``。

    对应游戏内::

        itemType = (ItemKind)(presentId / PresentKindConvert)
        itemId   = (int)(presentId % PresentKindConvert)
    """
    return present_id // PRESENT_KIND_CONVERT, present_id % PRESENT_KIND_CONVERT


def pack_present(item_kind: int, item_id: int) -> int:
    """把 ``(itemKind, itemId)`` 合成复合礼物 ID。"""
    return item_kind * PRESENT_KIND_CONVERT + item_id


class _ModifiedItem:
    __slots__ = ("item_id", "stock", "is_new")

    def __init__(self, item_id: int, stock: int, is_new: bool):
        self.item_id = item_id
        self.stock = stock
        self.is_new = is_new


class ItemSet:
    """一批道具，按 ``itemId`` 索引。

    :param data: 道具列表
    :param item_kind: 道具种类，导出时写入 ``itemKind``
    :param present: 是否为礼物。为真时导出会把 ``itemId`` 重新打包成复合值。
    """

    def __init__(
        self,
        data: Iterable[Mapping[str, Any]] = (),
        item_kind: int = UserItemKind.Music,
        present: bool = False,
    ) -> None:
        self._item: dict[int, int] = {}
        self._modified: dict[int, _ModifiedItem] = {}
        self.item_kind = item_kind
        self.present = present

        for detail in data:
            self._item[int(detail["itemId"])] = int(detail.get("stock") or 0)

    # ---- 读取 -----------------------------------------------------------
    def get(self, item_id: int) -> int | None:
        """返回库存数量；道具不存在时返回 ``None``。"""
        return self._item.get(item_id)

    def keys(self):
        return self._item.keys()

    def values(self) -> Iterator[int]:
        return iter(self._item.values())

    def items(self):
        return iter(self._item.items())

    def __iter__(self) -> Iterator[int]:
        return iter(self._item)

    def __len__(self) -> int:
        return len(self._item)

    def __contains__(self, item_id: object) -> bool:
        return item_id in self._item

    # ---- 写入 -----------------------------------------------------------
    def set(self, item_id: int, stock: int) -> None:
        """设置库存。已存在的标 ``isNew=False``，新增的标 ``isNew=True``。"""
        if item_id in self._item:
            self._item[item_id] = stock
            self._track(item_id, stock, is_new=False)
        else:
            self._item[item_id] = stock
            self._track(item_id, stock, is_new=True)

    # ---- 导出 -----------------------------------------------------------
    def export(self) -> list[dict[str, Any]]:
        result = []
        for modified in self._modified.values():
            result.append(
                {
                    "value": {
                        "itemKind": (
                            UserItemKind.Present if self.present else self.item_kind
                        ),
                        "itemId": (
                            pack_present(self.item_kind, modified.item_id)
                            if self.present
                            else modified.item_id
                        ),
                        "stock": modified.stock,
                        "isValid": True,
                    },
                    "isNew": modified.is_new,
                }
            )
        return result

    @property
    def modified_count(self) -> int:
        return len(self._modified)

    # ---- 内部 -----------------------------------------------------------
    def _track(self, item_id: int, stock: int, is_new: bool) -> None:
        existing = self._modified.get(item_id)
        if existing is not None:
            existing.stock = stock
            existing.is_new = existing.is_new or is_new
        else:
            self._modified[item_id] = _ModifiedItem(item_id, stock, is_new)
