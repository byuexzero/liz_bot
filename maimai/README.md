# maimai

舞萌 DX (maimai DX) / SDGB 服务端 API 客户端。

以 **eaquira 的流程逻辑**为主干，融合 **Lionheart** 的基板加密、类型系统与容器层，
并把全部密钥与敏感参数外置到 `.env`。

---

## 快速开始

```bash
# 1. 准备配置
cp .env.example .env
# 编辑 .env，填入密钥与凭据

# 2. 安装依赖
pip install -r requirements.txt

# 3. 确认配置加载正常
python -c "from maimai import config; import json; print(json.dumps(config.summary(), ensure_ascii=False, indent=2))"
```

```python
from maimai import MaimaiClient

async with MaimaiClient() as client:
    preview = await client.request("GetUserPreviewApi", {
        "userId": 12345678,
        "segaIdAuthKey": "",
        "token": "…",
        "clientId": "…",
    })
```

上号工作流：

```python
import asyncio
from maimai import run_workflow

result = asyncio.run(run_workflow(
    music_data={"musicId": 1145, "level": 3, "achievement": 1005000,
                "comboStatus": 1, "syncStatus": 1, "deluxscoreMax": 100},
    qr_code="SGWCMAID…",
))
print(result)            # [OK] 完成: 上号流程已完整执行，上传 1 条容器变更
print(result.stages)     # ['扫码', 'Preview', 'UserLogin', '装载用户数据',
                         #  '登记成绩', '等待游玩时长', 'UpsertUserAll', 'UserLogout']
```

`music_data` 里的 `scoreRank` 会被**忽略**——它由达成率推导（只读）。
`playCount` 也不是传入值，而是在服务端已有值的基础上 `+1`。

---

## 目录结构

```
maimai/
├── config.py              配置加载（.env）
├── encryption/
│   ├── maimai.py          业务接口：AES-CBC + zlib + MD5 混淆
│   └── aime.py            基板接口：AES-CBC + 随机 IV
├── client.py              业务客户端（重试 / 代理 / 连接池，同步 + 异步）
├── qr.py                  A.I.M.E. 扫码接口
├── payloads.py            简单请求体构造
├── user_data.py           用户数据聚合：容器驱动的增量上传
├── workflow.py            上号工作流
├── containers/            容器层（脏跟踪）
│   ├── score.py           成绩 + 评级换算
│   ├── character.py       角色
│   ├── item.py            道具 + 礼物打包
│   └── mission.py         任务
├── typings/               26 个接口的全量类型定义
│   ├── api.py             Request / Response
│   └── base.py            基础结构体与枚举
└── crawler/               批量数据抓取
    ├── updatedata.py      单个 userId 抓取落库
    └── crawler.py         线程池批量驱动
```

---

## 配置

所有密钥与敏感参数都在项目根目录的 `.env`。必填项缺失时会在导入期直接报错并指出缺哪一项。

| 变量 | 说明 |
|---|---|
| `MAIMAI_AES_KEY` / `MAIMAI_AES_IV` | 业务接口 AES 密钥，随游戏版本变更 |
| `MAIMAI_OBFUSCATE_PARAM` | 混淆参数（1.40=`BEs2D5vW`，1.50~1.52=`B44df8yT`，1.53=`LatuAa81`） |
| `MAIMAI_ENCODING` | 与 `Mai-Encoding` 请求头同值 |
| `MAIMAI_REGION` | `Chn` = 中国版，`Exp` = 国际版。会拼进混淆名 |
| `CHIP_ID` / `CLIENT_ID` | 基板 ID |
| `AIME_SALT` / `AIME_AES_KEY_HEX` | 扫码接口签名盐值与基板握手密钥 |
| `USER_ID` / `QR_CODE` | 账号凭据。**QR_CODE 等同账号密码** |
| `REGION_ID` / `REGION_NAME` / `PLACE_ID` / `PLACE_NAME` | 机厅信息 |
| `PROXY_URL` | 可选代理，留空直连。支持 `http://` 与 `socks5://` |
| `REQUEST_TIMEOUT` / `REQUEST_RETRIES` | 网络参数 |
| `INSECURE_TLS` | 仅调试用 |

`AIME_AES_KEY_HEX` 用十六进制而非明文，是因为该密钥含引号和反斜杠，
直接写进 `.env` 容易被解析器误处理。

---

## 相比旧实现的改动

| 项 | 旧实现 | 本实现 |
|---|---|---|
| **密钥与凭据** | 硬编码在 `settings.py` / `encrypt.py` | 全部外置到 `.env` |
| **基板 IV** | 固定全零（密码学缺陷） | **随机 IV**（Lionheart 做法） |
| **扫码时间戳** | 丢弃二维码自带值，用当前时间重新生成 | **优先提取二维码内的时间戳** |
| **`time.sleep(60)`** | 在 `async def` 里阻塞事件循环 | `await asyncio.sleep()` |
| **`UserPlaylog_payload`** | eaquira 重构时丢失，`ticket.py` 必然 `NameError` | playlog 并入 `UpsertUserAllApi`，不再需要该函数 |
| **SOCKS5 代理** | 硬编码在源码里 | `.env` 的 `PROXY_URL`，可选 |
| **重试** | 无 | 按 `REQUEST_RETRIES` 重试，指数退避 |
| **失败处理** | 静默返回 `None` | 抛 `MaimaiApiError`，可区分故障类型 |
| **API 类型** | 无（裸 dict） | 26 个接口的全量 TypedDict |
| **上传逻辑** | `payloads.build_user_all` 硬编码整个请求体 | **容器驱动**：`UserData` 只导出被改动过的条目 |
| **角色 / 道具上传** | 永远是空数组 | 走容器增量导出，带 `isNew` 标记 |
| **一次投币多曲** | 只支持单曲 | 按 4 首/投币分批（`iter_upsert_batches`） |
| **判定明细** | 硬编码某一首特定乐曲的物量（`tapCriticalPerfect: 101` 等） | 默认全 0，由调用方按谱面传入；`totalCombo` 不再是与判定数不自洽的 128 |
| **加密代码副本** | 4 份 | 1 份 |

### 关于扫码时间戳的改动

二维码结构为 `前缀(8) + 时间戳(12) + 载荷(64)`。旧实现取末 64 字符作为载荷、
但时间戳用当前时间重新生成；本实现优先从二维码里提取时间戳，
签名与该二维码严格对应。只有解析不出时间戳时才退回本地时间。

---

## 容器层用法

### 直接用容器

```python
from maimai.containers import ScoreSet
from maimai.typings.base import MusicDifficultyID

score_set = ScoreSet(user_music_detail_list)   # 从 GetUserMusicApi 的结果构造
score = score_set.get(1145)
score[MusicDifficultyID.Master].achievement = 1005000

score_set.export()   # 只有被改过的条目，可直接喂给 UpsertUserAllApi
```

核心是**脏跟踪**：只有改动过的成绩才会出现在 `export()` 里。

### 完整流程（推荐）

`UserData` 把「拉取 → 装容器 → 改数据 → 导出请求体」串成一条链：

```python
from maimai import MaimaiClient, PlayInput, UserData

async with MaimaiClient() as client:
    ud = await UserData.load(client, user_id=12345678)

    # 改成绩（会自动登记为待上传变更）
    ud.apply_play(PlayInput(music_id=1145, level=3, achievement=1005000))

    # 改角色 / 道具也一样，都会进增量
    ud.character.get(1).awakening = 3
    ud.items[1].set(100, 42)

    body = ud.build_upsert(
        login_id=login_id,
        login_date=login_date,
        login_date_time=login_date_time,
    )
    await client.request("UpsertUserAllApi", body, user_id)
```

导出的请求体里，`userMusicDetailList` / `userCharacterList` / `userItemList`
**只含被改动过的条目**，并用 `isNewMusicDetailList` 之类的 `"0"/"1"` 串
标记每个槽位是「覆盖已有」（`0`）还是「追加新槽位」（`1`）。

> **一次投币最多 4 首。** 待上传成绩超过 4 条时 `build_upsert()` 会报错，
> 请改用 `iter_upsert_batches()`，并在批次之间重新登录。
> 角色与道具只在第一个批次上传。

### 判定明细要自己填

**判定数、连击数、同步数都与具体乐曲的谱面绑定**，不存在一组能代表所有歌的常量。
所以 `DEFAULT_NOTE_COUNTS` 只保留 27 个键的**结构**，数值一律为 `0`——
不填就是全 0，不会替你编造游玩数据。

```python
from maimai import PlayInput

play = PlayInput(
    music_id=1145,
    level=3,
    achievement=1005000,
    deluxscore_max=2100,
    # 25 个判定字段，按实际谱面填
    note_counts={
        "tapCriticalPerfect": 500, "tapPerfect": 10, "tapGreat": 2,
        "holdCriticalPerfect": 100,
        "slideCriticalPerfect": 50,
        "touchCriticalPerfect": 30,
        "breakCriticalPerfect": 20,
    },
)
```

- 只给**部分**判定项也可以，缺的自动补 0（上传的 playlog 始终是完整结构）
- `total_combo` / `max_sync` 不传时按判定明细**求和**
- `max_combo` / `total_sync` / `ext_num4` 不传时为 `0`
- 以上都能在 `PlayInput` 上显式覆盖，优先级高于推导

`calculate_deluxscore(note_counts)` 可按判定明细算 DX 分
（CriticalPerfect ×3、Perfect ×2、Great ×1）。

> `PlayInput.from_mapping()` 也接受把 25 个判定字段**平铺**在字典里，
> 会自动收集；`scoreRank` 会被忽略（由达成率推导）。

---

## 批量抓取

```bash
python -m maimai.crawler.crawler --preview-db ./preview.db --output-db ./userdata.db
```

数据库表结构与旧版完全一致，已有的 `.db` 文件可直接继续使用。

---

## 与 QQ 机器人的关系

`maimai.qr` 目前**未被机器人调用**。`liz_bot` 的 `/qr` 指令已于 2026-09-22
暂时移除（它会对舞萌服务端发包）。恢复方法见 `liz_bot/_已移除功能_舞萌发包.md`。

> 注：`qr_api` 是**同步**函数。在事件循环内调用会阻塞整个循环。
> 异步场景请用 `maimai.qr.qr_api_async`。
> 若要恢复 `/qr`，应直接接 `qr_api_async`，不要再接同步版本。

---

## 设计决策

### 端点保持硬编码（2026-09-22）

`client.py` 的请求 URL 仍为硬编码拼接，不做 Lionheart 式的 allnet
`/net/initialize` 动态握手发现。已确认**保留此设计**。

- 可通过 `.env` 的 `MAIMAI_BASE_URL` / `AIME_QR_URL` 覆盖，服务器变更无需改代码
- 理由：本工具面向固定的国服 SDGB 端点，动态发现会多一次网络往返并扩大失败面，
  收益不足
- 若日后确有跟随官方端点迁移的需求，再考虑引入握手发现

---

## 已知待办

- 二维码凭据已多次以明文落入文件，建议轮换。
- `userMapList` / `userLoginBonusList` / `userCourseList` / `userGhost` 等
  仍是空数组（Lionheart 上游同样标了 `TODO: lazy update`），未走容器增量。
- `userFavoritemusicList` 与 `userFavoriteList` 尚未接
  `GetUserFavoriteApi` / `GetUserFavoriteItemApi`。
