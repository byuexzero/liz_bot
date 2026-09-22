"""容器层。

把接口返回的裸 dict 包装成带**脏跟踪**的集合对象，
只导出被改动过的条目，供 ``UpsertUserAllApi`` 使用。

移植自 Lionheart ``src/containers/``：

===================  ==============================
Lionheart            maimai.containers
===================  ==============================
``Score.ts``         :mod:`maimai.containers.score`
``Character.ts``     :mod:`maimai.containers.character`
``Item.ts``          :mod:`maimai.containers.item`
``Mission.ts``       :mod:`maimai.containers.mission`
===================  ==============================

典型用法::

    from maimai.containers import ScoreSet, MusicDifficultyID

    score_set = ScoreSet(music_detail_list)
    score = score_set.get(1145)
    score[MusicDifficultyID.Master].achievement = 1005000

    score_set.export()   # 只有被改过的条目
"""

from .character import Character, CharacterSet
from .item import ItemSet, pack_present, unpack_present
from .mission import Mission
from .score import (
    DIFFICULTY_LEVELS,
    Score,
    ScoreEntry,
    ScoreSet,
    convert_achievement_to_score_rank,
)

__all__ = [
    "Score",
    "ScoreEntry",
    "ScoreSet",
    "DIFFICULTY_LEVELS",
    "convert_achievement_to_score_rank",
    "Character",
    "CharacterSet",
    "ItemSet",
    "pack_present",
    "unpack_present",
    "Mission",
]
