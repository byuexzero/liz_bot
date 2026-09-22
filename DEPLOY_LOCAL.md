# 本地部署验证（上线前的彩排）

> **结论：能，而且这是最该做的一步。**
> 云端跑通只是把本地这一遍重复一次；反过来，凭据、指令、订阅上的问题
> 在本地就能发现，比在云平台日志里翻要快得多。

配套文档：[`DEPLOY_CLAWCLOUD.md`](DEPLOY_CLAWCLOUD.md)（云端步骤）、
[`README.md`](README.md)（部署提示）。

---

## 0. 一句话版本

```bash
python _tools/local_rehearsal.py
```

然后在 QQ 群里 @机器人 发 `/id 8`。**再跑一次同样的命令**，看它是否
告诉你「卷上已有 alias.json，本次不会覆盖」—— 两次都符合预期，就可以上云了。

---

## 1. 为什么要"彩排"而不是直接 `python run.py`

直接跑能启动，但它**证明不了云端能跑**。云端与本地只有两处差异，
而这两处恰好都是**静默失败**的重灾区 —— 不报错，但数据没了 / 容器被反复重启：

| 维度 | 本地直接跑 | 云端（ClawCloud Run） | 怎么在本地模拟 |
|---|---|---|---|
| 数据目录 | 仓库内，天然持久 | 挂载卷，不挂就**每次重启清空** | `LIZ_DATA_DIR` |
| 监听端口 | 不监听，无所谓 | 平台按「有没有端口」判活 | `HEALTHZ_PORT` |
| 文件系统 | 持久 | 临时的（重新部署即清） | 用独立目录当"卷" |
| 系统依赖 | 完整开发机 | `python:3.10-slim` | **只能**用真 Docker 验 |
| 进程模型 | 前台，Ctrl+C 就停 | 平台托管，可能被回收重启 | — |

`local_rehearsal.py` 把前两行的变量按云端的取值设好再启动，
所以**本地这一遍 = 云端那一遍的预演**。第三行用独立目录当卷，
于是"重启后数据还在吗"也能在本地问出答案。

---

## 2. 三层验证（能力递增，建议都做）

### L1 —— 进程能起来吗？

```bash
python _tools/local_rehearsal.py --plain
```

`--plain` 不设任何环境变量，等价于 `python run.py`。

**看什么**：启动后应打印 5 行路径信息，且**没有 traceback**。

```
数据来源：仓库内目录（未设 LIZ_DATA_DIR）
别名表：D:\projectsega\liz_bot\maimaiDX_songs\alias.json
...
健康检查已监听 0.0.0.0:...   ← 仅彩排模式才有
```

**证明了**：依赖齐全、Python 版本对、配置能加载、路径解析正确。

### L2 —— 云端配置路径对不对？（**最关键**）

```bash
python _tools/local_rehearsal.py
```

**看什么**（三件事，缺一不可）：

1. `数据来源：数据卷 ...` —— 而不是"仓库内目录"
2. `空卷首次启动，已播种基线别名表` —— 空卷会自动播种，否则别名全空
3. 浏览器打开 `http://localhost:18734/` → `{"status":"ok","bot":"starting"}`

**证明了**：`LIZ_DATA_DIR` 生效、空卷播种正确、探活端口能响应。

> `bot` 字段从 `starting` 变 `ready`，说明 WebSocket 已连上腾讯服务器 ——
> 这同时是**凭据有效 + 网络可达**的证据。本地这一层能看到 `ready`，
> 云端就没有理由看不到。

### L3 —— 群里真能干活吗？

机器人起来后，在 QQ 群里 @它：

| 操作 | 期望结果 | 证明了 |
|---|---|---|
| 发 `/id 8` | 返回《True Love Song》 | 指令链路 + 曲库完整 |
| 发 `随便一句话` | 返回「未知指令」而非静默 | 异常分支有回复 |
| 新增一条别名 | 成功写入 | 写盘路径可写 |

**再跑一次** `python _tools/local_rehearsal.py`，应看到：

```
卷上已有 alias.json（361538 字节）→ 本次**不会**覆盖它
```

**这是全流程里最重要的一条断言**：它证明「用户攒的别名在重启后不丢」。
云端最容易出的事故就是这一条 —— 容器重启，别名无声消失。

---

## 3. 本地 vs 云端：差异与对齐

彩排通过后，云端只需把两个变量换成云端的值：

| 变量 | 本地彩排取值 | 云端取值 |
|---|---|---|
| `QQ_BOT_APPID` | 环境变量 或 `liz_bot/config/config.yaml` | 平台环境变量 |
| `QQ_BOT_SECRET` | 同上 | 平台环境变量 |
| `LIZ_DATA_DIR` | `D:\projectsega\.local_volume` | **`/data`**（= 卷的挂载点） |
| `HEALTHZ_PORT` | `18734` | 平台注入的 `PORT`，或自定 |

⚠️ **`LIZ_DATA_DIR` 必须与卷的挂载点一致**。填成别的路径，数据就写到
容器临时层里 —— 不报错，但重启即丢。这是本项目第一号事故来源，
`verify_deploy_paths.py` 专门测这一条。

---

## 4. 常见失败信息对照

| 现象 | 含义 | 处理 |
|---|---|---|
| `RuntimeError: {'code': 100016, 'message': 'invalid appid or secret'}` | 凭据错误 | 核对 `QQ_BOT_APPID` / `QQ_BOT_SECRET` |
| `启动失败：缺少机器人配置：QQ_BOT_APPID、QQ_BOT_SECRET` | 两处都没取到凭据 | 设环境变量，或填 `config.yaml` |
| 端口被占用 | 上一次没退干净，或别处已跑 | `--port <其它端口>` |
| 连上后立刻被踢 / 反复掉线 | **同一个机器人不能两处同时在线** | 先停掉另一处（含云端实例） |
| `数据来源：仓库内目录` | `LIZ_DATA_DIR` 没生效 | 检查是否用了 `--plain` |
| 有 traceback 但能启动 | botpy 内部日志，未必是错 | 看是否影响 L3 |

---

## 5. 可选：用真 Docker 验（最忠实）

本机没装 Docker 也能做完上面三层；装了的话可以再补一层
**系统依赖**的验证（本地有完整开发环境，云端是 slim 镜像，
某些包在 slim 上可能缺编译工具）：

```bash
docker build -t liz-bot .
docker run --rm -v liz-data:/data \
  -e LIZ_DATA_DIR=/data \
  -e HEALTHZ_PORT=8080 -p 8080:8080 \
  -e QQ_BOT_APPID=<你的 AppID> \
  -e QQ_BOT_SECRET=<你的 AppSecret> \
  liz-bot
```

验证要点同上（L2 + L3）。**没有 Docker 也没关系** —— 依赖的
linux/amd64 wheel 兼容性已经用 `check_wheels.py` 静态验证过，
构建上下文也由 `simulate_container.py` 模拟跑通了。

---

## 6. 上线前自检清单

```bash
python _tools/local_rehearsal.py                  # L1 + L2（跑两次验持久化）
python _tools/verify_deploy_paths.py              # 卷与 LIZ_DATA_DIR 是否配对
python _tools/simulate_container.py               # .dockerignore 有没有误伤
python _tools/check_wheels.py                     # linux/amd64 依赖是否需要编译
```

四条全绿 + 群里 `/id 8` 有回复 → 可以按 [`DEPLOY_CLAWCLOUD.md`](DEPLOY_CLAWCLOUD.md) 上云。

---

## 附：`local_rehearsal.py` 参数

| 参数 | 说明 |
|---|---|
| （无） | 彩排：临时卷 + 健康检查端口，等价于云端 |
| `--plain` | 不设任何环境变量，等价于 `python run.py` |
| `--volume <目录>` | 用作数据卷的目录，默认 `.local_volume`。**复用同一目录即可验证持久化** |
| `--port <端口>` | 健康检查端口，默认 `18734` |
| `--no-healthz` | 不启用健康检查端口 |

脚本做的事：预检（Python 版本 / 依赖 / 凭据 / 端口）→ 准备卷 →
启动 `run.py` → 轮询探活端口并报告状态变化 → 退出时打印验证清单。
凭据只报长度，**不打印内容**。
