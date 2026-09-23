# Project Sega

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)

舞萌 DX (maimai DX) QQ 群机器人 + 国服 SDGB 服务端 API 工具链。

由两部分组成：

- **`liz_bot/`** —— QQ 群机器人。曲库检索、表情包、AI 对话、随机抽歌等指令。
- **`maimai/`** —— 舞萌 DX 服务端 API 客户端。加密层、扫码、用户数据容器、
  上号工作流、批量抓取。

---

## 环境要求

- **Python 3.10 ~ 3.12**（机器人运行于 3.10；部分模块用到 `str | Path` 等 3.10+ 语法）

## 安装

```bash
pip install -r requirements.txt
```

`socks5://` 代理需额外安装 `httpx[socks]`，`http(s)://` 代理不需要。

---

## 配置

配置**优先从环境变量读取**，不需要任何配置文件。

### 1. 机器人凭据（必填）

| 环境变量 | 说明 |
|---|---|
| `QQ_BOT_APPID` | QQ 开放平台（https://q.qq.com/）的机器人 AppID |
| `QQ_BOT_SECRET` | 机器人 AppSecret |

```bash
# Linux / macOS
export QQ_BOT_APPID=你的AppID
export QQ_BOT_SECRET=你的AppSecret
```

```powershell
# Windows PowerShell
$env:QQ_BOT_APPID="你的AppID"
$env:QQ_BOT_SECRET="你的AppSecret"
```

### 2. 千帆 AI 凭据（可选，仅 AI 变体 `qqgroup-ai-bot.py`）

| 环境变量 | 说明 |
|---|---|
| `QIANFAN_ACCESS_KEY` | 千帆 Access Key |
| `QIANFAN_SECRET_KEY` | 千帆 Secret Key |

留空时 AI 功能不可用，但机器人本身仍能正常启动。

### 3. 服务端 API 凭据（仅 `maimai/` 包用）

```bash
cp .env.example .env
# 填入 AES 密钥、基板 ID、userId、扫卡二维码等
```

### 本地 YAML 回退（可选）

不想设环境变量时也可用 YAML，但**环境变量优先级更高**，YAML 只用于补齐
环境变量中缺失的字段：

```bash
cp liz_bot/config/config.example.yaml liz_bot/config/config.yaml
# 另有 bot-config.example.yaml / ai_config.example.yaml，供 AI 变体使用
```

YAML 路径必须由调用方**显式传入**（见 `run.py`），不会自动探测；文件不存在
时静默跳过，不会掩盖"环境变量缺失"这个真正的错误。

`.env` 与 `liz_bot/config/*.yaml` 均已在 `.gitignore` 中，**不会入库**；
`*.example.yaml` 模板正常入库。

> ⚠️ 这些凭据等同账号密码。请勿提交、勿分享、勿贴进 issue。

### 部署提示

配置来源与文件路径都不依赖当前工作目录，可直接部署到云平台：

```
启动命令: python run.py
环境变量: QQ_BOT_APPID / QQ_BOT_SECRET
```

仓库中不含 `liz_bot/config/*.yaml`，云端 YAML 回退会自动跳过。缺少必填
环境变量时程序会打印一行清晰错误并退出（非 traceback），便于在平台日志里
定位。

注意本机器人需要**常驻长连接**，请选择不会因空闲而休眠的运行环境。
容器平台上还有两个变量建议设置：

| 环境变量 | 作用 | 不设的后果 |
|---|---|---|
| `LIZ_DATA_DIR` | 把可写数据（`ai_chat/`、`bot_log/`）挪出仓库（容器上是挂载卷，如 `/data`） | 容器重启后 AI 对话历史与运行日志**静默丢失** |
| `HEALTHZ_PORT` | 让容器监听一个端口供平台探活 | 可能被判为不健康而反复重启，表现为莫名掉线 |

两者都**不设置时行为与从前完全一致**，本地开发不受影响。设计动机分别见
`liz_bot/runtime_paths.py` 与 `liz_bot/healthz.py` 的模块文档。

> 📌 自 2026-09-23 起曲库与别名库都改为**只读**：曲库来自水鱼
> （`divingfish_songs/music_data.json`），别名来自柚子
> （`yuzuchan_aliases/aliases.json`），两者都随仓库分发。
> 歌曲别名的**写入**功能已停用 —— 柚子的别名是玩家众包投票产生的，
> 机器人只读快照。运行期**不再修改仓库内任何文件**，
> 卷上只需容纳 `ai_chat/` 与 `bot_log/`。

> 📦 **部署**：部署说明（本地彩排 / 国内平台 / 容器平台 共三份）
> **刻意不随仓库分发**，只在本地保留 —— 它们含具体的主机路径与逐步操作，
> 属于部署者自己的 runbook。
>
> 仓库内**随代码分发**的部署产物在 `deploy/` 下：systemd 单元、
> Docker Compose（内存/日志上限、持久化卷、探活端口）、凭据模板。
> 容器化所需的 `Dockerfile` / `.dockerignore` 在仓库根目录，
> 可直接用于任何容器平台。
>
> 规格余量结论（与那三份文档无关，可公开）：按实测推算，
> **入门型（2核2G / 4M / 50G）余量在 20 倍以上** ——
> 峰值内存 47.5 MB、镜像约 165 MB、日志 60 KB/天量级。

---

## 运行

```bash
python run.py
```

Windows 下可直接双击 `start.bat`。

**推荐用彩排脚本代替直接运行** —— 它会按云端的取值设好环境变量，
把云端才暴露的两类问题（数据目录、探活端口）提前到本地：

```bash
python _tools/local_rehearsal.py          # 彩排：临时卷 + 健康检查端口
python _tools/local_rehearsal.py --plain  # 等价于直接 python run.py
```

**跑两次**即可验证「重启后卷上数据不丢」（第二次会报告卷上已有 `ai_chat`、`bot_log` 且不会覆盖）。

用容器跑（本地复现线上形态）：

```bash
docker build -t liz-bot .
docker run --rm -v liz-data:/data -e LIZ_DATA_DIR=/data \
  -e HEALTHZ_PORT=8080 -p 8080:8080 \
  -e QQ_BOT_APPID=你的AppID -e QQ_BOT_SECRET=你的AppSecret liz-bot
```

---

## 目录结构

```
projectsega/
├── run.py                  启动入口
├── requirements.txt
├── Dockerfile              容器镜像（任意容器平台可用）
├── .dockerignore
├── deploy/                 VPS 部署产物（容器里用不到，已被 .dockerignore 排除）
│   ├── liz-bot.service     systemd 单元（venv 方式开机自启）
│   ├── docker-compose.yml  Docker 方式（内存/日志上限、卷、探活）
│   └── .env.example        容器版凭据模板（.env 本身不入库）
├── .env.example            API 凭据模板
├── 三套SDGB实现对比.md       sdgb / eaquira / Lionheart 三套旧实现的差异分析
├── liz_bot/                QQ 群机器人
│   ├── config.py           配置加载（环境变量优先，手动注入）
│   ├── runtime_paths.py    运行时数据目录（持久化卷支持）
│   ├── healthz.py          健康检查端口（容器平台探活）
│   ├── qqgroupbot.py       主入口（botpy）
│   ├── command_router.py   指令分发
│   ├── command_handler.py  对外 facade
│   ├── daily_funcs.py      日常指令实现
│   ├── song_query.py       曲库与别名检索（曲库：水鱼；别名：柚子）
│   ├── song_alias.py       别名**写入**（已停用，接口保留返回空数据）
│   ├── song_paths.py       曲库 / 别名库路径
│   ├── replies.py          回复文案载入（读 texts/replies.json，支持热更新）
│   ├── pic_haddler.py      表情包上传
│   ├── config/             配置模板与本地 YAML 回退
│   ├── divingfish_songs/   歌曲数据库（水鱼，随仓库分发）
│   ├── yuzuchan_aliases/   歌曲别名库（柚子，随仓库分发）
│   └── texts/              回复文案（replies.json，随仓库分发）
└── maimai/                 舞萌 DX API 客户端
    ├── config.py           .env 加载
    ├── client.py           业务接口客户端
    ├── qr.py               A.I.M.E. 扫码
    ├── user_data.py        用户数据聚合（容器驱动增量上传）
    ├── workflow.py         上号工作流
    ├── containers/         成绩 / 角色 / 道具 / 任务容器
    ├── typings/            接口类型定义
    └── crawler/            批量抓取
```

---

## 歌曲数据库与别名库

两份数据都**已随仓库分发**，克隆后开箱可用：

| 文件 | 内容 | 支撑的指令 |
|---|---|---|
| `liz_bot/divingfish_songs/music_data.json`（约 900KB） | 曲目、谱面、难度 | `/id`、`/song`、`/name`、`/songdata` |
| `liz_bot/yuzuchan_aliases/aliases.json`（约 250KB） | 每首歌的别名列表 | `/别名查歌`、`/查询别名`、`/cbm`，以及 `/song` 的别名兜底 |

### 曲库 —— 水鱼（diving-fish）

公开接口，免鉴权、无需 Token：

```bash
python _tools/fetch_music_data.py          # 增量更新（带 ETag，未变化时命中 304）
python _tools/fetch_music_data.py --force  # 强制重新下载
```

### 别名库 —— 柚子（yuzuchan）

水鱼**不提供**歌曲别名（它的 `alias` 只出现在"查询参数别名"里）。社区别名由
柚子单独维护，且是**玩家众包投票**产生的：提交申请 → 群友投票 → 过审入库。
同样是公开接口：

```bash
python _tools/fetch_aliases.py               # 更新（内容一致时不改写文件）
python _tools/fetch_aliases.py --force       # 强制重新下载
python _tools/fetch_aliases.py --url <地址>  # 换数据源（默认 .moe 域名，可换 .cn）
```

柚子的 `song_id` 与水鱼 `music_data.json` 的 `id` **完全一致**（SD 为原始 id，
DX 为 `id + 10000`，宴会场 ≥ 100000），可直接关联，**不需要偏移**。
（注意落雪 lxns 用的是另一套 ID 空间，需要 `+10000` 换算，本脚本不涉及。）

别名匹配照搬柚子语义：**大小写不敏感的精确匹配**，不是子串 ——
`真爱` 能命中，`真` 不能。另外别名按"歌名 → ID → 别名"的顺序兜底查询，
所以纯数字别名（如 `9`，属于 302）不会劫持 `/song 9`。

两个脚本用的是同一套策略：先完整校验响应（必需字段、条数下限、与曲库的关联
覆盖率），任一不通过就报错退出且**不覆盖**本地文件 —— 避免一次网关错误毁掉好数据。
柚子偶尔会返回 522 或长时间无响应，脚本对这类"可能只是抖动"的错误会自动退避重试。

> 上游均为第三方数据源，更新时机取决于其维护节奏；游戏新版本上线后
> 需要手动跑一次上面的命令再重新部署。

为控制仓库体积，以下素材**未纳入版本管理**，克隆后需要自行补齐：

| 目录 | 体积 | 影响 | 恢复方式 |
|---|---|---|---|
| `liz_bot/emoji_gif/` | ~34MB | 当前**代码中无任何引用**，属冗余素材 | 可不放 |
| `liz_bot/emoji/` | ~1MB | **当前无影响** —— `pic_haddler.py` 没有任何调用方，模块根本不会被导入 | 放入 PNG / JPG 即可；日后接线后，模块导入时自动扫描该目录 |

代码本身不依赖这些素材，缺失时只是对应指令不可用。

---

## 回复文案

机器人返回给群里的**所有提示性文本**都集中在
[`liz_bot/texts/replies.json`](liz_bot/texts/replies.json) —— 包括查歌排版模板、
"没有找到"、参数错误、未知指令、随机搭话等。源码里不再硬编码这些字面量，
改文案不需要动代码：

```jsonc
{
  "song": {
    "not_found": "Liz没有找到这样的歌",
    "format": "\n乐曲名称：{title} \n曲师：{artist} \nid：{id} \nmaster难度：{master_ds} ..."
  },
  "bot": { "none_reply": ["干什么！", "Liz在哦", "..."] }
}
```

几个刻意的设计：

- **改完立刻生效，不用重启** —— 载入模块按文件 mtime 判断是否重读（见
  `liz_bot/replies.py`）。调试文案时改一行、群里发一句就能看到结果。
- **文件缺失 / JSON 损坏 / 缺键 / 占位符写错 → 启动即报错退出**，并给出修复提示。
  **没有**代码内兜底文案 —— 否则会出现"明明改了文件却毫无变化"这种最难查的情况。
- **占位符是带名字的**（`{title}`、`{aliases}`、`{cmd_name}` …），不是位置参数，
  所以文案里换个语序不会串位。`format` 那 7 个占位符缺一个都会在启动时被拦下。
- 老代码里引用过的常量名（`song_query.NOT_FOUND`、`daily_funcs.HELP_TEXT`、
  `song_alias.ADD_FAILED` 等）**仍然可用**，只是改成实时读取，
  因此 `command_router.py` 与自检脚本无需改动。

`replies.json` 与曲库、别名库一样**随仓库/镜像分发**（见 `.gitignore` 的
`liz_bot/texts/` 一节）；它是**必需文件**，容器里缺了会直接启动失败，
这一条由 `_tools/verify_deploy_paths.py` 的「回复文本」一组断言守着。

---

## 未纳入版本管理的内容

| 路径 | 原因 |
|---|---|
| `.env`、`liz_bot/config/*.yaml` | 含真实凭据（`*.example.yaml` 模板正常入库） |
| `liz_bot/emoji*/` | 体积大 / 冗余素材 |
| `legacy_sdgb_stack/` | 旧实现存档（约 200MB），且内含明文凭据 |
| `legacy_qqbot_stack/` | 旧 QQ 技术栈（go_cqhttp + unidbg-fetch-qsign） |
| `_tools/` | 开发期工具（类型转换器、验证脚本）。**例外**：六个脚本随仓库分发（`verify_deploy_paths.py` / `simulate_container.py` / `check_wheels.py` / `local_rehearsal.py` / `fetch_music_data.py` / `fetch_aliases.py`） |
| `_backup/` | 备份 |
| `bot_log/` | 运行日志 |

---

## 关于舞萌发包功能

机器人侧的舞萌服务端发包功能（`/qr` 扫码）已于 2026-09-22 **暂时移除**。
`maimai/` 包本身完好，恢复步骤见
[`liz_bot/_已移除功能_舞萌发包.md`](liz_bot/_已移除功能_舞萌发包.md)。

---

## 鸣谢

本项目的实现与数据均建立在以下上游项目之上，在此致谢。

「**移植**」指按其逻辑**重写**为 Python（非直接复制源码），
但**设计、算法与踩过的坑**归功于上游作者 —— 没有它们，本项目无从起步。

### 服务端 API 实现（`maimai/` 包）

| 上游 | 作者 | 本项目用到的部分 | 许可 |
|---|---|---|---|
| [**Lionheart**](https://git.fragrance.moe/Furry12/Lionheart) | Furry12（FurryR） | 基板加密（**随机 IV**）、容器层、类型系统<br>`maimai/encryption/aime.py`、`maimai/containers/*` | **AGPL-3.0-only** ⚠️ |
| [**eaquira**](https://git.fragrance.moe/Fragrance/eaquira) | Project Fragrance | 流程主干、payload、业务加密<br>`maimai/client.py`、`maimai/payloads.py`、`maimai/encryption/maimai.py` | GNU GPL 系（上游未附许可证文件） |
| **sdgb** | — | 批量抓取<br>`maimai/crawler/*` | 上游未标明 |

> ⚠️ **Lionheart 采用 AGPL-3.0-only，而本项目对它是「移植」** ——
> 翻译 / 重写属于**衍生作品**，必须沿用同一许可。这不是偏好而是约束：
> AGPL-3.0 是强著佐权许可。因此**本项目整仓以 AGPL-3.0 授权**
> （见 [`LICENSE`](LICENSE) 与[许可](#许可)）。
> 第 13 条另要求：**若以网络服务形式提供，须向使用者提供对应源码**
> —— 本机器人正是长连接网络服务，**这条实际生效**。

### 数据来源

| 上游 | 作者 | 用途 | 许可 |
|---|---|---|---|
| [**水鱼查分器**（diving-fish）](https://www.diving-fish.com/maimaidx/prober) · [maimaidx-prober](https://github.com/Diving-Fish/maimaidx-prober) | Diving-Fish | 曲库 `divingfish_songs/music_data.json`<br>`GET /api/maimaidxprober/music_data` | MIT |
| [**柚子**（YuzuChaN）](https://github.com/Yuri-YuzuChaN/maimaiDX) · [别名 API 文档](https://bot.yuzuchan.moe/api/maimaiDX.html) | Yuri-YuzuChaN | 别名库 `yuzuchan_aliases/aliases.json`<br>`GET /api/v2/aliases/maimaidx/aliases` | MIT |

两者都是上游**公开接口**的只读快照，随仓库分发（见[歌曲数据库与别名库](#歌曲数据库与别名库)）。
柚子的别名由**玩家众包投票**产生 —— 向所有参与投票的玩家致谢。

### 运行依赖

| 依赖 | 用途 |
|---|---|
| [qq-botpy](https://github.com/tencent-connect/botpy) | 腾讯官方 QQ 机器人 SDK（`import botpy`） |
| httpx · pycryptodome · pytz · tqdm · python-dotenv<br>aiohttp · requests · urllib3 · PyYAML · qianfan · aiofiles | 见 [`requirements.txt`](requirements.txt) |

### 历史项目（已被取代，一并致谢）

| 上游 | 作者 | 说明 |
|---|---|---|
| [go-cqhttp](https://github.com/Mrs4s/go-cqhttp) · [unidbg-fetch-qsign](https://github.com/fuqiuluo/unidbg-fetch-qsign) | Mrs4s / fuqiuluo | 项目早期的 QQ 技术栈，2026-09-21 删除 |
| [maimaiDX-CN-songs-database](https://github.com/CrazyKidCN/maimaiDX-CN-songs-database) | CrazyKidCN | 旧曲库，2026-09-23 被水鱼曲库取代 |
| [dxrating](https://github.com/gekichumai/dxrating) · [MaimaiData](https://github.com/PaperPig/MaimaiData) | gekichumai / PaperPig | 旧曲库的上游数据源 |
| 落雪查分器（lxns） | — | 仅作 ID 空间对照，**未接入**本项目 |

---

## 许可

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
**GNU Affero General Public License v3.0** —— 全文见 [`LICENSE`](LICENSE)

```
Copyright (C) 2026 byuexzero
```

选择 AGPL-3.0 **不是偏好，而是约束**：`maimai/` 包移植自
[Lionheart](https://git.fragrance.moe/Furry12/Lionheart)（AGPL-3.0-only），
而移植 / 翻译属于**衍生作品**，必须沿用同一许可（见[鸣谢](#鸣谢)）。
`liz_bot/` 与 `maimai/` 合为一个可运行的整体，故**整仓**统一以 AGPL-3.0 发布。

| 你可以 | 但必须 |
|---|---|
| 自由使用、修改、分发 | 保留版权声明，并**沿用 AGPL-3.0** —— 衍生作品不得改为闭源 |
| 用于商业目的 | 提供完整的对应源码 |
| 部署为网络服务 | **向使用者提供对应源码**（AGPL 第 13 条） |

> 📌 **第 13 条对本项目是实际生效的**：`liz_bot` 是 24×7 在线的 QQ 群机器人，
> 属于「用户通过网络与之交互」的服务。**一旦你把它开放给他人使用，
> 就必须让使用者能拿到源码** —— 指向本仓库即可。
> 仅在本地自用、不对外提供服务，则不触发这一条。

> ⚠️ `legacy_sdgb_stack/` 与 `_backup/` 下的**旧实现、旧数据不在本许可范围内**：
> 它们未随仓库分发，其中还包含上游第三方代码与明文凭据。

---

## 免责声明

![免责声明](https://img.shields.io/badge/%E5%85%8D%E8%B4%A3%E5%A3%B0%E6%98%8E-%E8%AF%B7%E5%8A%A1%E5%BF%85%E9%98%85%E8%AF%BB-red?style=for-the-badge)

> [!CAUTION]
> **<font color="red">本项目仅供学习、研究与个人使用，请勿用于任何商业用途。</font>**
>
> **<font color="red">本项目与世嘉（SEGA）、华立科技（WAHLAP）及「舞萌 DX」官方
> 无任何关联，未获其授权、认可或支持。</font>**
>
> **<font color="red">使用本项目可能违反游戏服务条款，存在账号被封禁的风险。
> 一切后果由使用者自行承担，作者不承担任何责任。</font>**
>
> **<font color="red">请勿滥用上游公开接口。曲库、别名库与游戏数据的著作权
> 归各自权利人所有。</font>**

展开说明：

- **`maimai/` 包**会构造并发送与官方服务器通信的请求，此类行为**可能违反服务条款**。
  上游 eaquira 的原话是「WE ARE NOT RESPONSIBLE FOR YOUR ACCOUNT / 怂别用，用别怂」，
  本项目持同样立场：**怂别用，用别怂**。
- **曲库与别名库**来自第三方公开接口，本项目只做只读快照，
  对其**准确性、完整性、时效性不作任何保证**；上游随时可能变更或停止服务。
- **许可**：本项目以 **AGPL-3.0** 授权（`maimai/` 包是 AGPL-3.0 代码的衍生作品，
  见[鸣谢](#鸣谢)）。可自由使用、修改、分发，但**必须沿用同一许可**；
  且**若以网络服务形式提供，须向使用者提供对应源码**（AGPL 第 13 条）。
  详见[许可](#许可)。
- 曲名、谱面、定数等游戏内容，以及「舞萌 DX」「maimai DX」等名称与标识，
  著作权与商标权归**世嘉 / 华立科技**所有。
