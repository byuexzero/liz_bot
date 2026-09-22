# Project Sega

舞萌 DX (maimai DX) QQ 群机器人 + 国服 SDGB 服务端 API 工具链。

由两部分组成：

- **`liz_bot/`** —— QQ 群机器人。曲库检索、表情包、AI 对话、随机抽歌等指令。
- **`maimai/`** —— 舞萌 DX 服务端 API 客户端。加密层、扫码、用户数据容器、
  上号工作流、批量抓取。详见 [`maimai/README.md`](maimai/README.md)。

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
定位。注意本机器人需要**常驻长连接**，请选择不会因空闲而休眠的运行环境。

---

## 运行

```bash
python run.py
```

Windows 下可直接双击 `start.bat`。

---

## 目录结构

```
projectsega/
├── run.py                  启动入口
├── requirements.txt
├── .env.example            API 凭据模板
├── 三套SDGB实现对比.md       sdgb / eaquira / Lionheart 三套旧实现的差异分析
├── liz_bot/                QQ 群机器人
│   ├── config.py           配置加载（环境变量优先，手动注入）
│   ├── qqgroupbot.py       主入口（botpy）
│   ├── command_router.py   指令分发
│   ├── command_handler.py  对外 facade
│   ├── daily_funcs.py      日常指令实现
│   ├── song_query.py       曲库检索
│   ├── song_alias.py       曲目别名
│   ├── pic_haddler.py      表情包上传
│   ├── config/             配置模板与本地 YAML 回退
│   └── maimaiDX_songs/     歌曲数据库（随仓库分发）
└── maimai/                 舞萌 DX API 客户端，见 maimai/README.md
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

## 缺失素材

查歌数据 `liz_bot/maimaiDX_songs/`（`songs.json` + `alias.json`）**已随仓库分发**，
克隆后 `/id`、`/bm`、`/song`、`/name` 等指令开箱可用。

为控制仓库体积，以下素材**未纳入版本管理**，克隆后需要自行补齐：

| 目录 | 体积 | 影响 | 恢复方式 |
|---|---|---|---|
| `liz_bot/emoji_gif/` | ~34MB | 当前**代码中无任何引用**，属冗余素材 | 可不放；若日后启用，文件名需与代码引用一致 |
| `liz_bot/emoji/` | ~1MB | 缺少则静态表情上传指令不可用 | 放入 PNG / JPG 文件即可，`pic_haddler.py` 启动时自动扫描该目录 |

代码本身不依赖这些素材，缺失时只是对应指令不可用。

---

## 未纳入版本管理的内容

| 路径 | 原因 |
|---|---|
| `.env`、`liz_bot/config/*.yaml` | 含真实凭据（`*.example.yaml` 模板正常入库） |
| `liz_bot/emoji*/` | 体积大 / 冗余素材 |
| `legacy_sdgb_stack/` | 旧实现存档（约 200MB），且内含明文凭据 |
| `legacy_qqbot_stack/` | 旧 QQ 技术栈（go_cqhttp + unidbg-fetch-qsign） |
| `_tools/` | 开发期工具（类型转换器、验证脚本） |
| `_backup/` | 备份 |
| `bot_log/` | 运行日志 |

---

## 关于舞萌发包功能

机器人侧的舞萌服务端发包功能（`/qr` 扫码）已于 2026-09-22 **暂时移除**。
`maimai/` 包本身完好，恢复步骤见
[`liz_bot/_已移除功能_舞萌发包.md`](liz_bot/_已移除功能_舞萌发包.md)。

---

## 许可与免责

本项目仅供学习与研究使用，与世嘉（SEGA）、华立科技无任何关联。
请自行评估使用风险，遵守相关服务条款。
