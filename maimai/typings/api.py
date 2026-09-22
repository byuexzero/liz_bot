"""从 Lionheart 的 TypeScript 类型定义自动生成 —— 请勿手工编辑。

生成器：``_tools/convert_typings.py``
来源：``legacy_sdgb_stack/lionheart-src/src/typings/api``

共 1 个枚举、63 个 TypedDict。
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any, Literal, TypedDict

__all__ = [
    "CardItem",
    "FavoriteItem",
    "GetUserActivityApiRequest",
    "GetUserActivityApiResponse",
    "GetUserCardApiRequest",
    "GetUserCardApiResponse",
    "GetUserCharacterApiRequest",
    "GetUserCharacterApiResponse",
    "GetUserChargeApiRequest",
    "GetUserChargeApiResponse",
    "GetUserChargeApiResponseUserChargeList",
    "GetUserCourseApiRequest",
    "GetUserCourseApiResponse",
    "GetUserDataApiRequest",
    "GetUserDataApiResponse",
    "GetUserExtendApiRequest",
    "GetUserExtendApiResponse",
    "GetUserFavoriteApiRequest",
    "GetUserFavoriteApiResponse",
    "GetUserFavoriteItemApiRequest",
    "GetUserFavoriteItemApiResponse",
    "GetUserGhostApiRequest",
    "GetUserGhostApiResponse",
    "GetUserItemApiRequest",
    "GetUserItemApiResponse",
    "GetUserLoginBonusApiRequest",
    "GetUserLoginBonusApiResponse",
    "GetUserMapApiRequest",
    "GetUserMapApiResponse",
    "GetUserMissionDataApiRequest",
    "GetUserMissionDataApiResponse",
    "GetUserMusicApiRequest",
    "GetUserMusicApiResponse",
    "GetUserOptionApiRequest",
    "GetUserOptionApiResponse",
    "GetUserPreviewApiRequest",
    "GetUserPreviewApiResponse",
    "GetUserRatingApiRequest",
    "GetUserRatingApiResponse",
    "GetUserRecommendRateMusicApiRequest",
    "GetUserRecommendRateMusicApiResponse",
    "GetUserRecommendSelectMusicApiRequest",
    "GetUserRecommendSelectMusicApiResponse",
    "GetUserRegionApiRequest",
    "GetUserRegionApiResponse",
    "LogoutType",
    "Playlog",
    "RecommendRateMusicItem",
    "RegionItem",
    "ScoreItem",
    "UploadUserPlaylogListApiRequest",
    "UploadUserPlaylogListApiResponse",
    "UpsertUserAllApiRequest",
    "UpsertUserAllApiRequestUpsertUserAll",
    "UpsertUserAllApiRequestUpsertUserAllUser2pPlaylog",
    "UpsertUserAllApiRequestUpsertUserAllUser2pPlaylogUser2pPlaylogDetailList",
    "UpsertUserAllApiRequestUpsertUserAllUserDataItem",
    "UpsertUserAllApiResponse",
    "UpsertUserChargelogApiRequest",
    "UpsertUserChargelogApiResponse",
    "UserLoginApiRequest",
    "UserLoginApiResponse",
    "UserLogoutApiRequest",
    "UserLogoutApiResponse",
]


# 本文件引用了 base.py 定义的类型，必须显式导入
from .base import (
    MusicClearRankID,
    MusicDifficultyID,
    PlayComboFlagID,
    PlaySyncFlagID,
    UdemaeID,
    UserActivity,
    UserCharacter,
    UserCharge,
    UserChargelog,
    UserCourse,
    UserDetail,
    UserExtend,
    UserFavorite,
    UserFavoriteItem,
    UserGamePlaylog,
    UserGetPoint,
    UserGhost,
    UserIntimate,
    UserItem,
    UserItemKind,
    UserKaleidxScope,
    UserLoginBonus,
    UserMap,
    UserMissionData,
    UserMusicDetail,
    UserOption,
    UserRating,
    UserShopStock,
    UserTradeItem,
    UserWeeklyData,
)


# ----------------------------------------------------------------------
# 枚举
# ----------------------------------------------------------------------


class LogoutType(IntEnum):
    None_ = 0
    Logout = 1
    Cancel = 2
    Error = 3
    TestIn = 4
    Quit = 5


# ----------------------------------------------------------------------
# 结构体
# ----------------------------------------------------------------------


class CardItem(TypedDict):
    cardId: int
    cardTypeId: int
    charaId: int
    mapId: int
    startDate: str
    endDate: str


class FavoriteItem(TypedDict):
    orderId: int
    id: int


class GetUserActivityApiRequest(TypedDict):
    userId: int


class GetUserActivityApiResponse(TypedDict):
    userActivity: UserActivity


class GetUserCardApiRequest(TypedDict):
    userId: int
    nextIndex: int
    maxCount: int


class GetUserCardApiResponse(TypedDict):
    userId: int
    length: int
    nextIndex: int
    userCardList: list[CardItem] | None
    serialIdList: list[Any] | None


class GetUserCharacterApiRequest(TypedDict):
    userId: int


class GetUserCharacterApiResponse(TypedDict):
    userId: int
    length: int
    userCharacterList: list[UserCharacter] | None


class GetUserChargeApiRequest(TypedDict):
    userId: int


class GetUserChargeApiResponse(TypedDict):
    userId: int
    length: int
    userChargeList: list[GetUserChargeApiResponseUserChargeList] | None


class GetUserChargeApiResponseUserChargeList(TypedDict):
    chargeId: int
    stock: int
    purchaseDate: str
    validDate: str
    extNum1: int


class GetUserCourseApiRequest(TypedDict):
    userId: int
    nextIndex: int


class GetUserCourseApiResponse(TypedDict):
    userId: int
    length: int
    nextIndex: int
    userCourseList: list[UserCourse] | None


class GetUserDataApiRequest(TypedDict):
    userId: int


class GetUserDataApiResponse(TypedDict):
    userId: int
    userData: UserDetail
    banState: Literal[0, 1, 2]


class GetUserExtendApiRequest(TypedDict):
    userId: int


class GetUserExtendApiResponse(TypedDict):
    userId: int
    userExtend: UserExtend


class GetUserFavoriteApiRequest(TypedDict):
    userId: int
    itemKind: int


class GetUserFavoriteApiResponse(TypedDict):
    userId: int
    userFavorite: UserFavorite


class GetUserFavoriteItemApiRequest(TypedDict):
    userId: int
    kind: int
    nextIndex: int
    maxCount: int
    isAllFavoriteItem: bool


class GetUserFavoriteItemApiResponse(TypedDict):
    userId: int
    kind: int
    length: int
    nextIndex: int
    userFavoriteItemList: list[FavoriteItem] | None


class GetUserGhostApiRequest(TypedDict):
    userId: int


class GetUserGhostApiResponse(TypedDict):
    userId: int
    userGhostList: list[UserGhost]


class GetUserItemApiRequest(TypedDict):
    userId: int
    nextIndex: int
    maxCount: int


class GetUserItemApiResponse(TypedDict):
    userId: int
    length: int
    nextIndex: int
    itemKind: UserItemKind
    userItemList: list[UserItem] | None


class GetUserLoginBonusApiRequest(TypedDict):
    userId: int
    nextIndex: int
    maxCount: int


class GetUserLoginBonusApiResponse(TypedDict):
    userId: int
    length: int
    nextIndex: int
    userLoginBonusList: list[UserLoginBonus] | None


class GetUserMapApiRequest(TypedDict):
    userId: int
    nextIndex: int
    maxCount: int


class GetUserMapApiResponse(TypedDict):
    userId: int
    length: int
    nextIndex: int
    userMapList: list[UserMap] | None


class GetUserMissionDataApiRequest(TypedDict):
    userId: int


class GetUserMissionDataApiResponse(TypedDict):
    userId: int
    userWeeklyData: UserWeeklyData
    userMissionDataList: list[UserMissionData]


class GetUserMusicApiRequest(TypedDict):
    userId: int
    nextIndex: int
    maxCount: int


class GetUserMusicApiResponse(TypedDict):
    userId: int
    length: int
    nextIndex: int
    userMusicList: list[ScoreItem] | None


class GetUserOptionApiRequest(TypedDict):
    userId: int


class GetUserOptionApiResponse(TypedDict):
    userId: int
    userOption: UserOption


class GetUserPreviewApiRequest(TypedDict):
    userId: int
    segaIdAuthKey: str
    token: str
    clientId: str


class GetUserPreviewApiResponse(TypedDict):
    userId: int
    userName: str
    isLogin: bool
    lastGameId: int | None
    lastRomVersion: str
    lastDataVersion: str
    lastLoginDate: str
    lastPlayDate: str
    playerRating: int
    nameplateId: int
    iconId: int
    trophyId: int
    isNetMember: Literal[1, 0]
    isInherit: bool
    totalAwake: int
    dispRate: int
    dailyBonusDate: str
    headPhoneVolume: int | None
    banState: Literal[0, 1, 2]


class GetUserRatingApiRequest(TypedDict):
    userId: int


class GetUserRatingApiResponse(TypedDict):
    userId: int
    userRating: UserRating


class GetUserRecommendRateMusicApiRequest(TypedDict):
    userId: int


class GetUserRecommendRateMusicApiResponse(TypedDict):
    userId: int
    userRecommendRateMusicIdList: list[RecommendRateMusicItem]


class GetUserRecommendSelectMusicApiRequest(TypedDict):
    userId: int


class GetUserRecommendSelectMusicApiResponse(TypedDict):
    userId: int
    userRecommendSelectionMusicIdList: list[int]


class GetUserRegionApiRequest(TypedDict):
    userId: int


class GetUserRegionApiResponse(TypedDict):
    userId: int
    length: int
    userRegionList: list[RegionItem] | None


class Playlog(TypedDict):
    userId: int
    orderId: int
    playlogId: int
    version: int
    placeId: int
    placeName: str
    loginDate: int
    playDate: str
    userPlayDate: str
    type: int
    musicId: int
    level: MusicDifficultyID
    trackNo: int
    vsMode: int
    vsStatus: int
    vsUserName: str
    vsUserRating: int
    vsUserAchievement: int
    vsUserGradeRank: int
    vsRank: int
    playerNum: int
    playedUserId1: int
    playedUserName1: str
    playedMusicLevel1: MusicDifficultyID
    playedUserId2: int
    playedUserName2: str
    playedMusicLevel2: MusicDifficultyID
    playedUserId3: int
    playedUserName3: str
    playedMusicLevel3: MusicDifficultyID
    characterId1: int
    characterAwakening1: int
    characterLevel1: int
    characterId2: int
    characterAwakening2: int
    characterLevel2: int
    characterId3: int
    characterAwakening3: int
    characterLevel3: int
    characterId4: int
    characterAwakening4: int
    characterLevel4: int
    characterId5: int
    characterAwakening5: int
    characterLevel5: int
    achievement: int
    deluxscore: int
    scoreRank: MusicClearRankID
    maxCombo: int
    totalCombo: int
    maxSync: int
    totalSync: int
    tapCriticalPerfect: int
    tapPerfect: int
    tapGreat: int
    tapGood: int
    tapMiss: int
    holdCriticalPerfect: int
    holdPerfect: int
    holdGreat: int
    holdGood: int
    holdMiss: int
    slideCriticalPerfect: int
    slidePerfect: int
    slideGreat: int
    slideGood: int
    slideMiss: int
    touchCriticalPerfect: int
    touchPerfect: int
    touchGreat: int
    touchGood: int
    touchMiss: int
    breakCriticalPerfect: int
    breakPerfect: int
    breakGreat: int
    breakGood: int
    breakMiss: int
    isTap: bool
    isHold: bool
    isSlide: bool
    isTouch: bool
    isBreak: bool
    isCriticalDisp: bool
    isFastLateDisp: bool
    fastCount: int
    lateCount: int
    isAchieveNewRecord: bool
    isDeluxscoreNewRecord: bool
    comboStatus: PlayComboFlagID
    syncStatus: PlaySyncFlagID
    isClear: bool
    beforeRating: int
    afterRating: int
    beforeGrade: int
    afterGrade: int
    afterGradeRank: UdemaeID
    beforeDeluxRating: int
    afterDeluxRating: int
    isPlayTutorial: bool
    isEventMode: bool
    isFreedomMode: bool
    playMode: int
    isNewFree: bool
    trialPlayAchievement: int
    extNum1: int
    extNum2: int
    extNum4: int
    extBool1: bool
    extBool2: bool


class RecommendRateMusicItem(TypedDict):
    musicId: int
    level: MusicDifficultyID
    averageAchievement: int


class RegionItem(TypedDict):
    regionId: int
    playCount: int
    created: str


class ScoreItem(TypedDict):
    userMusicDetailList: list[UserMusicDetail] | None
    length: int


class UploadUserPlaylogListApiRequest(TypedDict):
    userId: int
    userPlaylogList: list[Playlog]


class UploadUserPlaylogListApiResponse(TypedDict):
    returnCode: int
    apiName: str


class UpsertUserAllApiRequest(TypedDict):
    userId: int
    playlogId: int
    isEventMode: bool
    isFreePlay: bool
    loginDateTime: int
    userPlaylogList: list[Playlog]
    upsertUserAll: UpsertUserAllApiRequestUpsertUserAll


class UpsertUserAllApiRequestUpsertUserAll(TypedDict):
    userData: list[UpsertUserAllApiRequestUpsertUserAllUserDataItem]
    userExtend: list[UserExtend]
    userOption: list[UserOption]
    userCharacterList: list[UserCharacter]
    userGhost: list[UserGhost]
    userMapList: list[UserMap]
    userLoginBonusList: list[UserLoginBonus]
    userRatingList: list[UserRating]
    userItemList: list[UserItem]
    userMusicDetailList: list[UserMusicDetail]
    userCourseList: list[UserCourse]
    userFriendSeasonRankingList: list[Any]
    userChargeList: list[UserCharge]
    userFavoriteList: list[UserFavorite]
    userActivityList: list[UserActivity]
    userMissionDataList: list[UserMissionData]
    userWeeklyData: UserWeeklyData
    userGamePlaylogList: list[UserGamePlaylog]
    user2pPlaylog: UpsertUserAllApiRequestUpsertUserAllUser2pPlaylog
    userIntimateList: list[UserIntimate]
    userShopItemStockList: list[UserShopStock]
    userGetPointList: list[UserGetPoint]
    userTradeItemList: list[UserTradeItem]
    userFavoritemusicList: list[UserFavoriteItem]
    userKaleidxScopeList: list[UserKaleidxScope]
    isNewCharacterList: str
    isNewMapList: str
    isNewLoginBonusList: str
    isNewItemList: str
    isNewMusicDetailList: str
    isNewCourseList: str
    isNewFavoriteList: str
    isNewFriendSeasonRankingList: str
    isNewUserIntimateList: str
    isNewFavoritemusicList: str
    isNewKaleidxScopeList: str


class UpsertUserAllApiRequestUpsertUserAllUser2pPlaylog(TypedDict):
    userId1: int
    userId2: int
    userName1: str
    userName2: str
    regionId: int
    placeId: int
    user2pPlaylogDetailList: list[UpsertUserAllApiRequestUpsertUserAllUser2pPlaylogUser2pPlaylogDetailList]


class UpsertUserAllApiRequestUpsertUserAllUser2pPlaylogUser2pPlaylogDetailList(TypedDict):
    musicId: int
    level: MusicDifficultyID
    achievement: int
    deluxscore: int
    userPlayDate: str


class UpsertUserAllApiRequestUpsertUserAllUserDataItem(UserDetail):
    banState: int


class UpsertUserAllApiResponse(TypedDict):
    returnCode: int
    apiName: str


class UpsertUserChargelogApiRequest(TypedDict):
    userId: int
    userChargelog: UserChargelog
    userCharge: UserCharge


class UpsertUserChargelogApiResponse(TypedDict):
    returnCode: int
    apiName: str


class UserLoginApiRequest(TypedDict):
    userId: int
    accessCode: str
    regionId: int
    placeId: int
    clientId: str
    dateTime: int
    loginDateTime: int
    isContinue: bool
    genericFlag: int
    token: str


class UserLoginApiResponse(TypedDict):
    returnCode: int
    loginDateTime: int
    token: str
    lastLoginDate: str
    loginCount: int
    consecutiveLoginCount: int
    loginId: int


class UserLogoutApiRequest(TypedDict):
    userId: int
    accessCode: str
    regionId: int
    placeId: int
    clientId: str
    loginDateTime: int
    type: LogoutType


class UserLogoutApiResponse(TypedDict):
    returnCode: int

# ----------------------------------------------------------------------
# 因 Python 关键字而改名的标识符（原名 -> 新名）
# ----------------------------------------------------------------------
#   enum LogoutType.None  ->  enum LogoutType.None_
