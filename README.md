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

### 1. 机器人凭据

```bash
cp liz_bot/config/config.example.yaml liz_bot/config/config.yaml
# 填入 QQ 开放平台的 appid / secret
```

另有 `bot-config.example.yaml`（AI 变体 `qqgroup-ai-bot.py` 用）与
`ai_config.example.yaml`（千帆大模型）。

### 2. 服务端 API 凭据

```bash
cp .env.example .env
# 填入 AES 密钥、基板 ID、userId、扫卡二维码等
```

`.env` 与 `liz_bot/config/*.yaml` 均已在 `.gitignore` 中，**不会入库**。

> ⚠️ 这些凭据等同账号密码。请勿提交、勿分享、勿贴进 issue。

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
│   ├── qqgroupbot.py       主入口（botpy）
│   ├── command_router.py   指令分发
│   ├── command_handler.py  对外 facade
│   ├── daily_funcs.py      日常指令实现
│   ├── song_query.py       曲库检索
│   ├── song_alias.py       曲目别名
│   ├── pic_haddler.py      表情包上传
│   ├── config/             机器人配置（真实配置不入库）
│   └── maimaiDX_songs/     歌曲数据库（不入库，见下）
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

为控制仓库体积，以下目录**未纳入版本管理**，克隆后需要自行补齐：

| 目录 | 体积 | 影响 | 恢复方式 |
|---|---|---|---|
| `liz_bot/maimaiDX_songs/` | ~2MB | 缺少则 `/id` 等曲库检索指令不可用 | 从上游 `maimaiDX-songs` 歌曲数据集获取，保持 `songs.json` / `alias.json` / `metadata.json` / `flevel.json` / `tags.json` / `version.json` 的原有结构 |
| `liz_bot/emoji_gif/` | ~34MB | 缺少则 GIF 表情指令不可用 | 自行放置，文件名与 `liz_bot/emoji_gif/` 内的引用一致 |
| `liz_bot/emoji/` | ~1MB | 缺少则静态表情不可用 | 同上 |

代码本身不依赖它们，缺失时只是对应指令不可用。

---

## 未纳入版本管理的内容

| 路径 | 原因 |
|---|---|
| `.env`、`liz_bot/config/*.yaml` | 含真实凭据 |
| `liz_bot/maimaiDX_songs/`、`liz_bot/emoji*/` | 体积大 / 第三方数据 |
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
