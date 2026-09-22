"""从 Lionheart 的 TypeScript 类型定义自动生成 —— 请勿手工编辑。

生成器：``_tools/convert_typings.py``
来源：``legacy_sdgb_stack/lionheart-src/src/typings/api/base``

共 31 个枚举、28 个 TypedDict。
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any, Literal, TypedDict

__all__ = [
    "ActivityCode",
    "MaiMileGetKind",
    "MissionLevelID",
    "MissionTypeID",
    "MusicClearRankID",
    "MusicDifficultyID",
    "OptionAppealID",
    "OptionCenterDisplayID",
    "OptionDispChainID",
    "OptionDispRateID",
    "OptionGameHoldID",
    "OptionGameOutlineID",
    "OptionGameSlideID",
    "OptionGameTapID",
    "OptionKindID",
    "OptionMirrorID",
    "OptionMovieBrightnessID",
    "OptionNoteSizeID",
    "OptionOutFrameDisplayID",
    "OptionSlideSizeID",
    "OptionStarTypeID",
    "OptionSubMonitorAchievementID",
    "OptionSubMonitorID",
    "OptionTouchEffectID",
    "OptionTouchSizeID",
    "OptionTrackSkipID",
    "PlayComboFlagID",
    "PlaySyncFlagID",
    "SortMusicID",
    "SortTabID",
    "UdemaeID",
    "UserAct",
    "UserActivity",
    "UserCharacter",
    "UserCharge",
    "UserChargelog",
    "UserCourse",
    "UserDetail",
    "UserExtend",
    "UserExtendEncountMapNpcList",
    "UserFavorite",
    "UserFavoriteItem",
    "UserGamePlaylog",
    "UserGetPoint",
    "UserGhost",
    "UserIntimate",
    "UserItem",
    "UserItemKind",
    "UserKaleidxScope",
    "UserLoginBonus",
    "UserMap",
    "UserMissionData",
    "UserMusicDetail",
    "UserOption",
    "UserRate",
    "UserRating",
    "UserRatingUdemae",
    "UserShopStock",
    "UserTradeItem",
    "UserWeeklyData",
]


# ----------------------------------------------------------------------
# 枚举
# ----------------------------------------------------------------------


class ActivityCode(IntEnum):
    PlayDX = 10
    RankS = 20
    RankSP = 21
    RankSS = 22
    RankSSP = 23
    RankSSS = 24
    RankSSSP = 25
    FullCombo = 30
    FullComboP = 31
    AllPerfect = 32
    AllPerfectP = 33
    FullSync = 40
    FullSyncP = 41
    FullSyncDx = 42
    FullSyncDxP = 43
    ClassUp_old = 50
    DxRate = 60
    AwakeMax = 70
    AwakePreMax = 71
    MapComplete = 80
    TransmissionMusic = 100
    TaskMusicClear = 110
    ChallengeMusicClear = 120
    RankUp = 130
    ClassUp = 140


class MaiMileGetKind(IntEnum):
    Mission = 1
    FriendBonus = 2
    Present = 3


class MissionLevelID(IntEnum):
    Level1 = 0
    Level2 = 1


class MissionTypeID(IntEnum):
    Login = 0
    MusicPlay = 1
    Buddy = 2


class MusicClearRankID(IntEnum):
    Rank_D = 0
    Rank_C = 1
    Rank_B = 2
    Rank_BB = 3
    Rank_BBB = 4
    Rank_A = 5
    Rank_AA = 6
    Rank_AAA = 7
    Rank_S = 8
    Rank_SP = 9
    Rank_SS = 10
    Rank_SSP = 11
    Rank_SSS = 12
    Rank_SSSP = 13


class MusicDifficultyID(IntEnum):
    Basic = 0
    Advanced = 1
    Expert = 2
    Master = 3
    ReMaster = 4
    Utage = 10


class OptionAppealID(IntEnum):
    Off = 0
    Together = 1
    Tiho = 2
    GoldPass = 3
    FullSync = 4
    AllPlay = 5


class OptionCenterDisplayID(IntEnum):
    Off = 0
    Combo = 1
    AchievementPlus = 2
    AchievementMinus1 = 3
    AchievementMinus2 = 4
    SBorder = 5
    SSBorder = 6
    SSSBorder = 7
    BestBorder = 8
    DeluxScore = 9
    DeluxScoreMinus = 10
    DeluxScoreStar = 11


class OptionDispChainID(IntEnum):
    Off = 0
    Achievement = 1
    Sync = 2


class OptionDispRateID(IntEnum):
    AllDisp = 0
    DispRateDan = 1
    DispRateClass = 2
    DispDanClass = 3
    DispRate = 4
    DispDan = 5
    DispClass = 6
    Hide = 7


class OptionGameHoldID(IntEnum):
    Default = 0
    Legacy = 1


class OptionGameOutlineID(IntEnum):
    Hide = 0
    Dot = 1
    Simple = 2
    Sensor = 3
    Maimai = 4
    GreeN = 5
    ORANGE = 6
    PiNK = 7
    MURASAKi = 8
    MiLK = 9
    FiNALE = 10
    DX = 11
    Splash = 12
    UNiVERSE = 13
    FESTiVAL = 14
    BUDDiES = 15
    PRiSM = 16


class OptionGameSlideID(IntEnum):
    Default = 0
    Legacy = 1


class OptionGameTapID(IntEnum):
    Default = 0
    Legacy = 1
    Bear = 2
    Bar = 3
    Any = 4


class OptionKindID(IntEnum):
    Basic = 0
    Advance = 1
    Expert = 2
    Custom = 3


class OptionMirrorID(IntEnum):
    Normal = 0
    LR = 1
    UD = 2
    UDLR = 3


class OptionMovieBrightnessID(IntEnum):
    Dark = 0
    Darker = 1
    Brighter = 2
    Bright = 3


class OptionNoteSizeID(IntEnum):
    Small = 0
    Middle = 1
    Big = 2


class OptionOutFrameDisplayID(IntEnum):
    Off = 0
    AchievementPlus = 1
    AchievementMinus1 = 2
    AchievementMinus2 = 3
    DxScorePlus = 4
    DxScoreMinus = 5
    FastLate = 6
    Judge = 7


class OptionStarTypeID(IntEnum):
    Blue = 0
    Red = 1


class OptionSubMonitorAchievementID(IntEnum):
    AchievementPlus = 0
    AchievementMinus = 1


class OptionSubMonitorID(IntEnum):
    AnimationType1 = 0
    CharacterOnly = 1
    AchievementOnly = 2


class OptionTouchEffectID(IntEnum):
    Off = 0
    Outline = 1
    On = 2


class OptionTouchSizeID(IntEnum):
    Small = 0
    Middle = 1


class OptionTrackSkipID(IntEnum):
    Off = 0
    Push = 1
    AutoS = 2
    AutoSS = 3
    AutoSSS = 4
    AutoBest = 5
    AutoLife300 = 6
    AutoLife100 = 7
    AutoLife50 = 8
    AutoLife10 = 9
    AutoLife1 = 10


class PlayComboFlagID(IntEnum):
    None_ = 0
    FullCombo = 1
    FullComboPlus = 2
    AllPerfect = 3
    AllPerfectPlus = 4


class PlaySyncFlagID(IntEnum):
    None_ = 0
    FullSync = 1
    FullSyncPlus = 2
    FullSyncDX = 3
    FullSyncDXPlus = 4
    SyncPlay = 5


class SortMusicID(IntEnum):
    ID = 0
    Level = 1
    Rank = 2
    ApFc = 3
    Sync = 4
    Name = 5
    DxScore = 6
    BPM = 7


class SortTabID(IntEnum):
    Genre = 0
    All = 1
    Version = 2
    Level = 3
    Name = 4
    Rank = 5


class UdemaeID(IntEnum):
    Class_B5 = 0
    Class_B4 = 1
    Class_B3 = 2
    Class_B2 = 3
    Class_B1 = 4
    Class_A5 = 5
    Class_A4 = 6
    Class_A3 = 7
    Class_A2 = 8
    Class_A1 = 9
    Class_S5 = 10
    Class_S4 = 11
    Class_S3 = 12
    Class_S2 = 13
    Class_S1 = 14
    Class_SS5 = 15
    Class_SS4 = 16
    Class_SS3 = 17
    Class_SS2 = 18
    Class_SS1 = 19
    Class_SSS5 = 20
    Class_SSS4 = 21
    Class_SSS3 = 22
    Class_SSS2 = 23
    Class_SSS1 = 24
    Class_LEGEND = 25


class UserItemKind(IntEnum):
    Plate = 1
    Title = 2
    Icon = 3
    Present = 4
    Music = 5
    MusicMas = 6
    MusicRem = 7
    MusicSrg = 8
    Character = 9
    Partner = 10
    Frame = 11
    Ticket = 12


# ----------------------------------------------------------------------
# 类型别名
# ----------------------------------------------------------------------


OptionSlideSizeID = OptionNoteSizeID

# ----------------------------------------------------------------------
# 结构体
# ----------------------------------------------------------------------


class UserAct(TypedDict):
    kind: Literal[1, 2]
    id: ActivityCode
    sortNumber: int
    param1: int
    param2: int
    param3: int
    param4: int


class UserActivity(TypedDict):
    playList: list[UserAct]
    musicList: list[UserAct]


class UserCharacter(TypedDict):
    characterId: int
    point: int
    level: int
    awakening: int
    useCount: int


class UserCharge(TypedDict):
    chargeId: int
    stock: int
    purchaseDate: str
    validDate: str


class UserChargelog(TypedDict):
    chargeId: int
    price: int
    purchaseDate: str
    playCount: int | None
    playerRating: int | None
    placeId: int
    regionId: int
    clientId: str


class UserCourse(TypedDict):
    courseId: int
    isLastClear: bool
    totalRestlife: int
    totalAchievement: int
    totalDeluxscore: int
    bestAchievement: int
    bestDeluxscore: int
    bestAchievementDate: str
    bestDeluxscoreDate: str
    playCount: int
    clearDate: str
    lastPlayDate: str
    extNum1: int


class UserDetail(TypedDict):
    accessCode: str | None
    userName: str
    isNetMember: Literal[1, 0]
    point: int
    totalPoint: int
    playerRating: int
    playerOldRating: int
    playerNewRating: int
    highestRating: int
    gradeRating: int
    musicRating: int
    gradeRank: UdemaeID
    courseRank: int
    classRank: int
    frameId: int
    iconId: int
    plateId: int
    titleId: int
    partnerId: int
    charaSlot: list[int]
    charaLockSlot: list[int]
    contentBit: int
    selectMapId: int
    playCount: int
    currentPlayCount: int
    playVsCount: int
    playSyncCount: int
    winCount: int
    helpCount: int
    comboCount: int
    totalDeluxscore: int
    totalBasicDeluxscore: int
    totalAdvancedDeluxscore: int
    totalExpertDeluxscore: int
    totalMasterDeluxscore: int
    totalReMasterDeluxscore: int
    totalSync: int
    totalBasicSync: int
    totalAdvancedSync: int
    totalExpertSync: int
    totalMasterSync: int
    totalReMasterSync: int
    totalAchievement: int
    totalBasicAchievement: int
    totalAdvancedAchievement: int
    totalExpertAchievement: int
    totalMasterAchievement: int
    totalReMasterAchievement: int
    eventWatchedDate: str
    lastGameId: str | None
    lastRomVersion: str
    lastDataVersion: str
    lastLoginDate: str
    lastPlayDate: str
    lastPairLoginDate: str
    lastTrialPlayDate: str
    lastPlayCredit: int
    lastPlayMode: int
    lastPlaceId: int
    lastPlaceName: str | None
    lastAllNetId: int
    lastRegionId: int
    lastRegionName: str
    lastClientId: str | None
    lastCountryCode: str
    lastSelectEMoney: int
    lastSelectTicket: int
    lastSelectCourse: int
    lastCountCourse: int
    firstGameId: str
    firstRomVersion: str
    firstDataVersion: str
    firstPlayDate: str
    compatibleCmVersion: str
    totalAwake: int
    dailyBonusDate: str
    dailyCourseBonusDate: str
    mapStock: int
    renameCredit: int
    friendRegistSkip: int
    dateTime: int | None


class UserExtend(TypedDict):
    selectMusicId: int
    selectDifficultyId: int
    categoryIndex: int
    musicIndex: int
    extraFlag: int
    selectScoreType: int
    selectResultDetails: bool
    selectResultScoreViewType: int
    sortCategorySetting: int
    sortMusicSetting: int
    selectedCardList: list[int]
    encountMapNpcList: list[UserExtendEncountMapNpcList]
    extendContentBit: int
    playStatusSetting: int


class UserExtendEncountMapNpcList(TypedDict):
    npcId: int
    musicId: int


class UserFavorite(TypedDict):
    userId: int | None
    itemKind: int
    itemIdList: list[int]


class UserFavoriteItem(TypedDict):
    orderId: int
    id: int


class UserGamePlaylog(TypedDict):
    playlogId: int
    version: str
    playDate: str
    playMode: int
    useTicketId: int
    playCredit: int
    playTrack: int
    clientId: str
    isPlayTutorial: bool
    isEventMode: bool
    isNewFree: bool
    playCount: int
    playSpecial: int
    playOtherUserId: int


class UserGetPoint(TypedDict):
    getKind: MaiMileGetKind
    point: int


class UserGhost(TypedDict):
    name: str
    iconId: int
    plateId: int
    titleId: int
    rate: int
    udemaeRate: int
    courseRank: int
    classRank: UdemaeID
    classValue: int
    playDatetime: str
    shopId: int
    regionCode: int
    typeId: MusicDifficultyID
    musicId: int
    difficulty: int
    version: int
    resultBitList: list[int]
    resultNum: int
    achievement: int


class UserIntimate(TypedDict):
    partnerId: int
    intimateLevel: int
    intimateCountRewarded: int


class UserItem(TypedDict):
    itemKind: UserItemKind
    itemId: int
    stock: int
    isValid: bool


class UserKaleidxScope(TypedDict):
    gateId: int
    isGateFound: bool
    isKeyFound: bool
    isClear: bool
    totalRestLife: int
    totalAchievement: int
    totalDeluxscore: int
    bestAchievement: int
    bestDeluxscore: int
    bestAchievementDate: str
    bestDeluxscoreDate: str
    playCount: int
    clearDate: str
    lastPlayDate: str
    isInfoWatched: bool


class UserLoginBonus(TypedDict):
    bonusId: int
    point: int
    isCurrent: bool
    isComplete: bool


class UserMap(TypedDict):
    mapId: int
    distance: int
    isLock: bool
    isClear: bool
    isComplete: bool
    unlockFlag: Literal[0, 1]


class UserMissionData(TypedDict):
    type: MissionTypeID
    difficulty: MissionLevelID
    targetGenreId: int
    targetGenreTableId: int
    conditionGenreId: int
    conditionGenreTableId: MusicClearRankID
    clearFlag: bool


class UserMusicDetail(TypedDict):
    musicId: int
    level: MusicDifficultyID
    playCount: int
    achievement: int
    comboStatus: PlayComboFlagID
    syncStatus: PlaySyncFlagID
    deluxscoreMax: int
    scoreRank: MusicClearRankID
    extNum1: int


class UserOption(TypedDict):
    optionKind: OptionKindID
    noteSpeed: int
    slideSpeed: int
    touchSpeed: int
    noteSize: OptionNoteSizeID
    slideSize: OptionSlideSizeID
    touchSize: OptionTouchSizeID
    tapDesign: OptionGameTapID
    holdDesign: OptionGameHoldID
    slideDesign: OptionGameSlideID
    starType: OptionStarTypeID
    starRotate: int
    adjustTiming: int
    judgeTiming: int
    mirrorMode: OptionMirrorID
    ansVolume: int
    tapHoldVolume: int
    touchHoldVolume: int
    breakVolume: int
    exVolume: int
    slideVolume: int
    breakSe: int
    slideSe: int
    exSe: int
    criticalSe: int
    tapSe: int
    headPhoneVolume: int
    matching: int
    brightness: OptionMovieBrightnessID
    dispRate: OptionDispRateID
    dispCenter: OptionCenterDisplayID
    dispJudge: int
    dispJudgePos: int
    dispJudgeTouchPos: int
    dispChain: OptionDispChainID
    dispBar: int
    trackSkip: OptionTrackSkipID
    touchEffect: OptionTouchEffectID
    outlineDesign: OptionGameOutlineID
    submonitorAnimation: OptionSubMonitorID
    submonitorAppeal: OptionAppealID
    submonitorAchive: OptionSubMonitorAchievementID
    sortTab: SortTabID
    sortMusic: SortMusicID
    damageSeVolume: int
    touchVolume: int
    outFrameType: OptionOutFrameDisplayID
    breakSlideVolume: int


class UserRate(TypedDict):
    musicId: int
    level: MusicDifficultyID
    romVersion: int
    achievement: int


class UserRating(TypedDict):
    rating: int
    ratingList: list[UserRate]
    newRatingList: list[UserRate]
    nextRatingList: list[UserRate]
    nextNewRatingList: list[UserRate]
    udemae: UserRatingUdemae


class UserRatingUdemae(TypedDict):
    maxLoseNum: int
    npcTotalWinNum: int
    npcTotalLoseNum: int
    npcMaxWinNum: int
    npcMaxLoseNum: int
    npcWinNum: int
    npcLoseNum: int
    rate: int
    classValue: int
    maxRate: int
    maxClassValue: int
    totalWinNum: int
    totalLoseNum: int
    maxWinNum: int
    winNum: int
    loseNum: int


class UserShopStock(TypedDict):
    shopItemId: int
    tradeCount: int


class UserTradeItem(TypedDict):
    shopItemId: int
    point: int
    tradeCount: int


class UserWeeklyData(TypedDict):
    lastLoginWeek: str
    beforeLoginWeek: str
    friendBonusFlag: bool

# ----------------------------------------------------------------------
# 因 Python 关键字而改名的标识符（原名 -> 新名）
# ----------------------------------------------------------------------
#   enum PlayComboFlagID.None  ->  enum PlayComboFlagID.None_
#   enum PlaySyncFlagID.None  ->  enum PlaySyncFlagID.None_
