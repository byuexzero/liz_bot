# Project Sega

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)

舞萌 DX（maimai DX）QQ 群机器人。纯 Python，基于腾讯官方 SDK `qq-botpy`。

## 功能

| 指令 | 说明 |
|---|---|
| `/help` | 指令列表 |
| `/hello` | 打个招呼 |
| `/random [最小] [最大]` | 掷骰子；给一组值则随机选一个 |
| `/id <歌曲id>` | 按 id 查歌 |
| `/songdata <歌曲id> <难度>` | 查该谱面各判定档位的扣分 |
| `/bm <别名>` | 按别名查歌 |
| `/name <歌名>` | 按歌名查歌 |
| `/song <歌名或别名>` | 按歌名或别名查歌 |
| `/查询别名 <歌名或别名>` | 查这首歌的别名 |

几个用起来才知道的设计：

- **大小写不敏感** —— `/HELP` 与 `/help` 等价。
- **多轮补参** —— 参数没给全时会追问「还差 N 个参数（参数名）」，你直接
  回一句就行，不必重敲整条指令。缺多个可以分次补，也可以一条消息按顺序给全
  （`/添加别名` → 回 `8 测试别名`）。
- **同名消歧** —— 同名不同版本（SD / DX）在曲库里是两条记录。命中多首时会
  列出候选，回**序号**或**id** 都能选，不会再默默给你其中一条。
- **`#` 命名空间** —— 舞萌相关指令的预留前缀（如 `#b50`），目前尚未接入，
  统一回复「未解析的指令」。

## 环境要求

Python 3.10 ~ 3.12（`qq-botpy` 目前只在 3.10 上验证过）。

## 安装

```bash
pip install -r requirements.txt
```

`socks5://` 代理需额外安装 `httpx[socks]`，`http(s)://` 代理不需要。

## 配置

凭据**优先从环境变量读取**，不需要任何配置文件：

| 环境变量 | 说明 |
|---|---|
| `QQ_BOT_APPID` | QQ 开放平台（<https://q.qq.com/>）的机器人 AppID |
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

不想设环境变量时也可用 YAML 回退（**环境变量优先级更高**）：

```bash
cp liz_bot/config/config.example.yaml liz_bot/config/config.yaml
```

YAML 路径必须由调用方**显式传入**（见 `run.py`），不会自动探测；文件不存在时
静默跳过，不会掩盖「环境变量缺失」这个真正的错误。

`.env` 与 `liz_bot/config/*.yaml` 均已在 `.gitignore` 中。**这些凭据等同账号
密码，请勿提交、勿分享、勿贴进 issue。**

## 运行

```bash
python run.py
```

Windows 下可直接双击 `start.bat`。

**推荐用彩排脚本代替直接运行** —— 它按云端的取值设好环境变量，把云端才暴露的
两类问题（数据目录、探活端口）提前到本地。**跑两次**即可验证「重启后卷上数据
不丢」：

```bash
python _tools/local_rehearsal.py          # 彩排：临时卷 + 健康检查端口
python _tools/local_rehearsal.py --plain  # 等价于直接 python run.py
```

## 部署

本机器人需要**常驻长连接**，请选择不会因空闲而休眠的运行环境。
配置来源与文件路径都不依赖当前工作目录，可直接部署到云平台。

```bash
docker build -t liz-bot .
docker run --rm -v liz-data:/data -e LIZ_DATA_DIR=/data \
  -e HEALTHZ_PORT=8080 -p 8080:8080 \
  -e QQ_BOT_APPID=你的AppID -e QQ_BOT_SECRET=你的AppSecret liz-bot
```

仓库内随代码分发的部署产物：

| 路径 | 用途 |
|---|---|
| `Dockerfile` / `.dockerignore` | 容器镜像，可用于任何容器平台 |
| `deploy/docker-compose.yml` | Docker 方式（内存/日志上限、持久化卷、探活） |
| `deploy/liz-bot.service` | systemd 单元（venv 方式开机自启） |
| `deploy/.env.example` | 容器版凭据模板 |

容器平台上另有两个变量建议设置：

| 环境变量 | 作用 | 不设的后果 |
|---|---|---|
| `LIZ_DATA_DIR` | 把可写数据（`ai_chat/`、`bot_log/`）挪出仓库（容器上是挂载卷，如 `/data`） | 容器重启后 AI 对话历史与运行日志**静默丢失** |
| `HEALTHZ_PORT` | 让容器监听一个端口供平台探活 | 可能被判为不健康而反复重启，表现为莫名掉线 |

两者都**不设置时行为与从前完全一致**，本地开发不受影响。设计动机分别见
`liz_bot/runtime_paths.py` 与 `liz_bot/healthz.py` 的模块文档。

> 📌 自 2026-09-23 起曲库与别名库都改为**只读**，两者都随仓库分发。
> 运行期**不再修改仓库内任何文件**，卷上只需容纳 `ai_chat/` 与 `bot_log/`。

> 📦 规格余量（按实测推算）：**入门型（2核2G / 4M / 50G）余量在 20 倍以上**
> —— 峰值内存 47.5 MB、镜像约 165 MB、日志 60 KB/天量级。

## 目录结构

```
projectsega/
├── run.py                  启动入口
├── requirements.txt
├── Dockerfile / .dockerignore
├── deploy/                 VPS 部署产物（systemd 单元 / compose / 凭据模板）
├── liz_bot/                QQ 群机器人
│   ├── qqgroupbot.py       主入口（botpy）：去重 / 空消息 / 补参会话键 / 异常兜底
│   ├── command_router.py   指令的唯一事实来源：指令表 / 解析 / 校验 / 分发 / 帮助
│   ├── pending.py          多轮补参的会话状态
│   ├── command_handler.py  兼容层 facade（只 re-export）
│   ├── song_query.py       曲库与别名检索、同名消歧
│   ├── song_alias.py       别名**写入**（已停用，接口保留返回空数据）
│   ├── judge_detail.py     谱面判定细节（/songdata）
│   ├── text_layout.py      显示宽度对齐 / 截断（CJK 算 2 格）
│   ├── replies.py          回复文案载入（读 texts/replies.json，支持热更新）
│   ├── config.py           配置加载（环境变量优先，手动注入）
│   ├── runtime_paths.py    运行时数据目录（持久化卷支持）
│   ├── healthz.py          健康检查端口（容器平台探活）
│   ├── daily_funcs.py      日常指令实现
│   ├── pic_haddler.py      表情包上传
│   ├── song_paths.py       曲库 / 别名库路径
│   ├── config/             配置模板与本地 YAML 回退
│   ├── divingfish_songs/   歌曲数据库（水鱼，随仓库分发）
│   ├── yuzuchan_aliases/   歌曲别名库（柚子，随仓库分发）
│   └── texts/              回复文案（replies.json，随仓库分发）
└── _tools/                 开发期工具（仅六个脚本随仓库分发，见下）
```

## 数据来源

三份数据都**已随仓库分发**，克隆后开箱可用：

| 文件 | 内容 | 支撑的指令 |
|---|---|---|
| `liz_bot/divingfish_songs/music_data.json`（约 900KB） | 曲目、谱面、难度、物量 | `/id`、`/song`、`/name`、`/songdata` |
| `liz_bot/yuzuchan_aliases/aliases.json`（约 250KB） | 每首歌的别名列表 | `/bm`、`/查询别名`、`/song` 的别名兜底 |
| `liz_bot/texts/replies.json`（约 5KB） | 全部用户可见文案 | 所有指令 |

### 曲库 —— 水鱼（diving-fish）

公开接口，免鉴权、无需 Token：

```bash
python _tools/fetch_music_data.py          # 增量更新（带 ETag，未变化时命中 304）
python _tools/fetch_music_data.py --force  # 强制重新下载
```

### 别名库 —— 柚子（yuzuchan）

水鱼**不提供**歌曲别名。社区别名由柚子单独维护，且是**玩家众包投票**产生的
（提交申请 → 群友投票 → 过审入库）：

```bash
python _tools/fetch_aliases.py               # 更新（内容一致时不改写文件）
python _tools/fetch_aliases.py --force       # 强制重新下载
python _tools/fetch_aliases.py --url <地址>  # 换数据源（默认 .moe 域名，可换 .cn）
```

柚子的 `song_id` 与水鱼的 `id` **完全一致**（SD 为原始 id，DX 为 `id + 10000`，
宴会场 ≥ 100000），可直接关联，**不需要偏移**。别名匹配照搬柚子语义：
**大小写不敏感的精确匹配**，不是子串 —— `真爱` 能命中，`真` 不能。

两个脚本用的是同一套策略：先完整校验响应（必需字段、条数下限、与曲库的关联
覆盖率），任一不通过就报错退出且**不覆盖**本地文件 —— 避免一次网关错误毁掉
好数据。

> 上游均为第三方数据源，更新时机取决于其维护节奏；游戏新版本上线后需要手动跑
> 一次上面的命令再重新部署。

### 判定分值 —— 网络来源

`/songdata` 输出的扣分，需要每种音符类型 × 每个判定档位的得分常量。曲库
`music_data.json` **不含**这些数值，它们来自**网络上流传的舞萌 DX 客户端
反编译产物**（`NoteScore.cs` 等文件里的 `judgeScoreTbl` 与计分函数）。

本项目**只读取其中的数值常量**，并据此复算达成率；**不包含、不分发该反编译
产物的任何代码或文件**，仓库里也没有任何代码依赖它。

复算结果已用真实成绩交叉验证：`id 143` Re:Master 按该分值表复算得
**99.598910%**，与机台实测精确吻合（差 < 1e-5）。这条断言固化在
`_tools/test_command_table.py` 里。

## 回复文案

机器人返回给群里的**所有提示性文本**都集中在
[`liz_bot/texts/replies.json`](liz_bot/texts/replies.json) —— 查歌排版模板、
「没有找到」、参数错误、未知指令、随机搭话等。源码里不硬编码这些字面量，
改文案不需要动代码。

几个刻意的设计：

- **改完立刻生效，不用重启** —— 载入模块按文件 mtime 判断是否重读
  （见 `liz_bot/replies.py`）。
- **文件缺失 / JSON 损坏 / 缺键 / 占位符写错 → 启动即报错退出**，并给出修复
  提示。**没有**代码内兜底文案 —— 否则会出现「明明改了文件却毫无变化」这种
  最难查的情况。
- **占位符是带名字的**（`{title}`、`{aliases}`、`{cmd_name}` …），不是位置参数，
  文案里换个语序不会串位。

## 未纳入版本管理的内容

| 路径 | 原因 |
|---|---|
| `.env`、`liz_bot/config/*.yaml` | 含真实凭据（`*.example.yaml` 模板正常入库） |
| `liz_bot/emoji*/` | 体积大 / 冗余素材 |
| `maimai/` | 舞萌 DX 服务端 API 工具链。**只在本地保留，不随仓库分发** —— 它是功能完整的上传工具，公开分发不合适。与机器人运行无关（`liz_bot/` 对它零 import） |
| `legacy_sdgb_stack/` | 旧实现存档（约 200MB），且内含明文凭据 |
| `legacy_qqbot_stack/` | 旧 QQ 技术栈（go_cqhttp + unidbg-fetch-qsign） |
| `_backup/` | 备份 |
| `bot_log/`、`ai_chat/` | 运行日志与 AI 会话历史（含真实群聊内容） |
| `_tools/` | 开发期工具。**例外**：六个脚本随仓库分发 —— `verify_deploy_paths.py` / `simulate_container.py` / `check_wheels.py` / `local_rehearsal.py` / `fetch_music_data.py` / `fetch_aliases.py` |

## 自检

改完代码建议跑一遍（都不联网、不起 bot）：

```bash
python _tools/verify_deploy_paths.py    # 持久化卷与 LIZ_DATA_DIR 是否配对、文案是否完整
python _tools/simulate_container.py     # .dockerignore 有没有误伤运行必需文件
python _tools/check_wheels.py           # 依赖在 linux/amd64 上是否需要源码编译
python _tools/local_rehearsal.py        # 用「云端的方式」在本地跑一遍
```

## 鸣谢

本项目的实现与数据均建立在上游项目之上，在此致谢。

| 上游 | 用途 |
|---|---|
| [qq-botpy](https://github.com/tencent-connect/botpy) | 腾讯官方 QQ 机器人 SDK（`import botpy`） |
| [水鱼查分器](https://github.com/Diving-Fish/maimaidx-prober) | 曲库数据源（MIT） |
| [柚子 maimaiDX](https://github.com/Yuri-YuzuChaN/maimaiDX) · [别名 API](https://bot.yuzuchan.moe/api/maimaiDX.html) | 别名库数据源（MIT）。别名由**玩家众包投票**产生，向所有参与投票的玩家致谢 |
| [go-cqhttp](https://github.com/Mrs4s/go-cqhttp) · [unidbg-fetch-qsign](https://github.com/fuqiuluo/unidbg-fetch-qsign) | 项目早期的 QQ 技术栈，2026-09-21 删除 |
| [maimaiDX-CN-songs-database](https://github.com/CrazyKidCN/maimaiDX-CN-songs-database) | 旧曲库，2026-09-23 被水鱼曲库取代 |

## 许可

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
**GNU Affero General Public License v3.0** —— 全文见 [`LICENSE`](LICENSE)

```
Copyright (C) 2026 byuexzero
```

| 你可以 | 但必须 |
|---|---|
| 自由使用、修改、分发 | 保留版权声明，并**沿用 AGPL-3.0** —— 衍生作品不得改为闭源 |
| 用于商业目的 | 提供完整的对应源码 |
| 部署为网络服务 | **向使用者提供对应源码**（AGPL 第 13 条） |

> 📌 **第 13 条对本项目是实际生效的**：`liz_bot` 是 24×7 在线的 QQ 群机器人，
> 属于「用户通过网络与之交互」的服务。**一旦你把它开放给他人使用，就必须让
> 使用者能拿到源码** —— 指向本仓库即可。仅在本地自用、不对外提供服务，
> 则不触发这一条。

## 免责声明

> [!CAUTION]
> **本项目仅供学习、研究与个人使用，请勿用于任何商业用途。**
>
> **本项目与世嘉（SEGA）、华立科技（WAHLAP）及「舞萌 DX」官方无任何关联，
> 未获其授权、认可或支持。**
>
> **使用本项目可能违反游戏服务条款，存在账号被封禁的风险。
> 一切后果由使用者自行承担，作者不承担任何责任。**
>
> **请勿滥用上游公开接口。曲库、别名库与游戏数据的著作权归各自权利人所有。**

展开说明：

- **曲库与别名库**来自第三方公开接口，本项目只做只读快照，对其**准确性、
  完整性、时效性不作任何保证**；上游随时可能变更或停止服务。
- **判定分值**来自网络上流传的客户端反编译产物，本项目只取其中的数值常量用于
  复算，**不分发该产物本身**，亦不对其准确性作保证。
- **许可**：本项目以 **AGPL-3.0** 授权。可自由使用、修改、分发，但**必须沿用
  同一许可**；且**若以网络服务形式提供，须向使用者提供对应源码**（第 13 条）。
- 曲名、谱面、定数等游戏内容，以及「舞萌 DX」「maimai DX」等名称与标识，
  著作权与商标权归**世嘉 / 华立科技**所有。
