"""任务容器。

移植自 Lionheart ``src/containers/Mission.ts``。

上游只做了归一化：周常日期缺失时填 ``1900-01-01 00:00:00``，
``friendBonusFlag`` 强制转布尔。本实现保持一致。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

__all__ = ["Mission", "DEFAULT_WEEK_DATE"]

#: 周常日期缺失时的占位值
DEFAULT_WEEK_DATE = "1900-01-01 00:00:00"


class Mission:
    """玩家当前的任务状态。

    :param weekly: ``UserWeeklyData``，缺失字段会被填默认值
    :param mission: ``UserMissionData`` 列表
    """

    __slots__ = ("weekly", "mission")

    def __init__(
        self,
        weekly: Mapping[str, Any] | None = None,
        mission: Iterable[Mapping[str, Any]] = (),
    ) -> None:
        weekly = weekly or {}
        self.weekly: dict[str, Any] = {
            "beforeLoginWeek": weekly.get("beforeLoginWeek") or DEFAULT_WEEK_DATE,
            "lastLoginWeek": weekly.get("lastLoginWeek") or DEFAULT_WEEK_DATE,
            "friendBonusFlag": bool(weekly.get("friendBonusFlag")),
        }
        self.mission: list[Mapping[str, Any]] = list(mission)

    def __repr__(self) -> str:
        return (
            f"Mission(lastLoginWeek={self.weekly['lastLoginWeek']!r}, "
            f"count={len(self.mission)})"
        )
