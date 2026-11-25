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
| `/估分 <歌曲id> <难度> <达成率>` | 按物量估分 |
| `/bm <别名>` | 按别名查歌 |
| `/name <歌名>` | 按歌名查歌 |
| `/song <歌名或别名>` | 按歌名或别名查歌 |
| `/查询别名 <歌名或别名>` | 查这首歌的别名 |

- **大小写不敏感** —— `/HELP` 等同 `/help`。
- **多轮补参** —— 参数不全时追问「还差 N 个参数」，回一句即可；也可一条消息给全。
- **同名消歧** —— SD / DX 同名是两条记录，命中多首时列出候选，回序号或 id 均可。
- **`#` 命名空间** —— 舞萌相关指令的前缀。

## 环境要求

Python 3.10 ~ 3.12

## 安装

```bash
pip install -r requirements.txt
```

`socks5://` 代理需额外安装 `httpx[socks]`，`http(s)://` 代理不需要。

## 配置

凭据**优先从环境变量读取**，不需要配置文件：

| 环境变量 | 说明 |
|---|---|
| `QQ_BOT_APPID` | QQ 开放平台（<https://q.qq.com/>）的机器人 AppID |
| `QQ_BOT_SECRET` | 机器人 AppSecret |

```bash
export QQ_BOT_APPID=你的AppID
export QQ_BOT_SECRET=你的AppSecret
```

Windows PowerShell 用 `$env:QQ_BOT_APPID="你的AppID"`。

也可用 YAML 回退（**环境变量优先**）：

```bash
cp liz_bot/config/config.example.yaml liz_bot/config/config.yaml
```

YAML 路径须由调用方**显式传入**（见 `run.py`），不自动探测；文件缺失时静默跳过。

`.env` 与 `liz_bot/config/*.yaml` 已忽略。**凭据等同账号密码，勿提交、勿分享。**

## 运行

```bash
python run.py
```

Windows 下可直接双击 `start.bat`。

```bash
python _tools/local_rehearsal.py          # 临时卷 + 健康检查端口
python _tools/local_rehearsal.py --plain  # 等价于直接 python run.py
```

## 部署

本机器人需要**常驻长连接**，请选择不会因空闲而休眠的运行环境。

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
| `deploy/docker-compose.yml` | compose 方式（内存/日志上限、持久化卷、探活） |
| `deploy/liz-bot.service` | systemd 单元（venv 方式开机自启） |
| `deploy/.env.example` | 容器版凭据模板 |

容器平台上建议再设两个变量：

| 环境变量 | 作用 | 不设的后果 |
|---|---|---|
| `LIZ_DATA_DIR` | 可写数据（`ai_chat/`、`bot_log/`）挪出仓库，容器上挂载卷 | 重启后对话历史与运行日志**静默丢失** |
| `HEALTHZ_PORT` | 供平台探活的监听端口 | 可能被判不健康而反复重启，表现为莫名掉线 |

两者不设置时行为与从前一致。设计动机见 `liz_bot/runtime_paths.py` 与
`liz_bot/healthz.py`。

> 📌 曲库与别名库自 2026-09-23 起**只读**并随仓库分发，运行期**不改仓库内任何
> 文件**，卷上只需 `ai_chat/` 与 `bot_log/`。

> 📦 规格余量（实测）：**入门型（2核2G / 4M / 50G）余量 20 倍以上** ——
> 峰值内存 47.5 MB、镜像约 165 MB、日志 60 KB/天。

## 目录结构

```
projectsega/
├── run.py                 启动入口
├── requirements.txt
├── Dockerfile / .dockerignore
├── deploy/                VPS 部署产物（systemd / compose / 凭据模板）
├── liz_bot/               QQ 群机器人
│   ├── qqgroupbot.py      主入口（botpy）：去重 / 补参会话键 / 异常兜底
│   ├── command_router.py  指令唯一事实来源：指令表 / 解析 / 校验 / 分发 / 帮助
│   ├── pending.py         多轮补参的会话状态
│   ├── song_query.py      曲库与别名检索、同名消歧
│   ├── judge_detail.py    谱面判定细节（/songdata）
│   ├── score_estimate.py  估分与出图（/估分）
│   ├── ai_chat.py         群聊 AI（只回 @）
│   ├── text_layout.py     显示宽度对齐 / 截断（CJK 算 2 格）
│   ├── replies.py         文案载入（mtime 热更新）
│   ├── runtime_paths.py   运行时数据目录（持久化卷）
│   ├── healthz.py         健康检查端口
│   ├── divingfish_songs/  曲库（随仓库分发）
│   ├── yuzuchan_aliases/  别名库（随仓库分发）
│   └── texts/             回复文案
└── _tools/                开发期工具（仅六个脚本随仓库分发，见「自检」）
```

## 数据来源

三份数据都**随仓库分发**，克隆后开箱可用：

| 文件 | 内容 | 支撑的指令 |
|---|---|---|
| `divingfish_songs/music_data.json`（约 900KB） | 曲目、谱面、难度、物量 | `/id`、`/song`、`/name`、`/songdata` |
| `yuzuchan_aliases/aliases.json`（约 250KB） | 每首歌的别名列表 | `/bm`、`/查询别名`、`/song` |
| `texts/replies.json`（约 5KB） | 全部用户可见文案 | 所有指令 |

**曲库（水鱼）** —— 公开接口，免鉴权：

```bash
python _tools/fetch_music_data.py          # 增量更新（ETag，未变化时 304）
python _tools/fetch_music_data.py --force  # 强制重新下载
```

**别名库（柚子）** —— 水鱼不提供别名，由柚子单独维护，玩家投票产生：

```bash
python _tools/fetch_aliases.py               # 更新（内容一致时不改写文件）
python _tools/fetch_aliases.py --force       # 强制重新下载
python _tools/fetch_aliases.py --url <地址>  # 默认 .moe 域名，可换 .cn
```

柚子 `song_id` 与水鱼 `id` 完全一致（SD 为原始 id，DX 为 `id + 10000`，
宴会场 ≥ 100000），**不需要偏移**。别名按**大小写不敏感的精确匹配**，`真爱`
能命中、`真` 不能。

两个脚本策略相同：先校验响应（必需字段、条数下限、与曲库的关联覆盖率），
任一不通过就报错退出且**不覆盖**本地文件，避免一次网关错误毁掉好数据。

> 上游为第三方数据源，更新取决于其维护节奏；游戏新版本上线后需手动跑一次再部署。

**判定分值** —— `/songdata` 与 `/估分` 所需的扣分常量内置在代码中。

## 回复文案

所有用户可见文本都集中在
[`liz_bot/texts/replies.json`](liz_bot/texts/replies.json)，源码不硬编码字面量。

- **改完立刻生效，不用重启** 按文件 mtime 判断是否重读。
- **文件缺失 / JSON 损坏 / 缺键 / 占位符写错 → 启动即报错退出**。
- **占位符带名字**（`{title}`、`{aliases}` …），换语序不会串位。

## 未纳入版本管理

| 路径 | 原因 |
|---|---|
| `.env`、`liz_bot/config/*.yaml` | 含真实凭据（`*.example.yaml` 模板正常入库） |
| `liz_bot/emoji*/` | 体积大 / 冗余素材 |
| `maimai/` | 舞萌 DX 服务端 API 工具链，**只在本地保留**。与机器人运行无关（`liz_bot/` 对它零 import） |
| `legacy_sdgb_stack/` | 旧实现存档（约 200MB），内含明文凭据 |
| `legacy_qqbot_stack/` | 旧 QQ 技术栈 |
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

## 创作者

| | GitHub |
|---|---|
| 项目作者 | [@byuexzero](https://github.com/byuexzero) |
| 共同创作者 | [@yuanweishupian535](https://github.com/yuanweishupian535) |

## 鸣谢

| 上游 | 用途 |
|---|---|
| [qq-botpy](https://github.com/tencent-connect/botpy) | 腾讯官方 QQ 机器人 SDK（`import botpy`） |
| [水鱼查分器](https://github.com/Diving-Fish/maimaidx-prober) | 曲库数据源（MIT） |
| [柚子 maimaiDX](https://github.com/Yuri-YuzuChaN/maimaiDX) · [别名 API](https://bot.yuzuchan.moe/api/maimaiDX.html) | 别名库数据源（MIT），别名由**玩家众包投票**产生，向所有参与投票的玩家致谢 |
| [go-cqhttp](https://github.com/Mrs4s/go-cqhttp) · [unidbg-fetch-qsign](https://github.com/fuqiuluo/unidbg-fetch-qsign) | 项目早期的 QQ 技术栈，2026-09-21 移除 |
| [maimaiDX-CN-songs-database](https://github.com/CrazyKidCN/maimaiDX-CN-songs-database) | 旧曲库，2026-09-23 被水鱼曲库取代 |

## 许可

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
**GNU Affero General Public License v3.0** —— 全文见 [`LICENSE`](LICENSE)

```
Copyright (C) 2026 byuexzero, yuanweishupian535
```

| 你可以 | 但必须 |
|---|---|
| 自由使用、修改、分发 | 保留版权声明，**沿用 AGPL-3.0**，衍生作品不得闭源 |
| 用于商业目的 | 提供完整的对应源码 |
| 部署为网络服务 | **向使用者提供对应源码**（第 13 条） |

> **第 13 条对本项目实际生效**：`liz_bot` 是 24×7 在线的群机器人，属于
> 「用户通过网络与之交互」的服务。**开放给他人使用就必须让使用者拿到源码** ——
> 指向本仓库即可；仅本地自用不触发。

## 免责声明

> [!CAUTION]
> **本项目仅供学习、研究与个人使用，请勿用于任何商业用途。**
>
> **与世嘉（SEGA）、华立科技（WAHLAP）及「舞萌 DX」官方无任何关联，
> 未获其授权、认可或支持。**
>
> **使用本项目可能违反游戏服务条款，存在账号被封禁的风险。
> 一切后果由使用者自行承担，作者不承担任何责任。**

- **曲库与别名库**来自第三方公开接口，本项目只做只读快照，对其**准确性、
  完整性、时效性不作任何保证**；上游随时可能变更或停止服务。
- 曲名、谱面、定数等游戏内容，以及「舞萌 DX」「maimai DX」等名称与标识，
  著作权与商标权归**世嘉 / 华立科技**所有。
