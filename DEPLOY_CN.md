# 部署到国内平台

> 适用于无法使用境外平台的场景。价格与活动均为 **2026 年 9 月**查到的公开信息，
> 促销随时会变，下单前请以官网当前页面为准。
>
> 配套：[`DEPLOY_LOCAL.md`](DEPLOY_LOCAL.md)（本地验证）、
> [`DEPLOY_PAAS.md`](DEPLOY_PAAS.md)（容器平台通用说明）。

---

## 0. 结论：买"轻量应用服务器"，不要用按量容器

一句话：**这个机器人必须 24 小时保持长连接，而按量计费的省钱机制恰恰
建立在"没流量时缩容到 0"上 —— 对你完全不适用。**

| 方案 | 规格 | 年费 | 说明 |
|---|---|---|---|
| **腾讯云轻量（首选）** | 4核4G / 3M / 40G | **¥38** 首年 | 个人新用户秒杀，0.5 折 |
| 腾讯云轻量 同价续费 | 2核2G / 4M / 50G | **¥99/年** | 第 2 年，新老同享，限 1 次 |
| 腾讯云轻量 服务器专区 | 2核4G / 6M / 70G | **¥528 / 3 年** | ≈¥14.7/月，长期最省 |
| 阿里云 ECS e 实例 | 2核2G / 3M / 40G | **¥99/年** | **新购续费同价**，不用抢 |
| Sealos（容器 PaaS） | 0.5核1G + 1G 卷 | ≈¥156/年 | 按量，省心但更贵 |
| Sealos（容器 PaaS） | 1核2G + 1G 卷 | ≈¥307/年 | 同上 |
| 腾讯云 CloudBase 云托管 | 1核2G 常驻 | ≈¥1700+/年 | 按量，**明显不划算** |

**推荐路径**：先用腾讯云轻量的 **0 元免费试用（2核2G / 1 个月）** 跑通，
满意后再上 **38 元秒杀（4核4G / 1 年）**。等于第一个月免费，之后 ¥38/年。

### 补充：别把"免费额度"当长期方案

一个真实案例。本文档最初推荐的 **ClawCloud Run**（凭 GitHub 账号满 180 天
每月送 $5 额度、免绑卡）已经不存在了：

| 时间 | 事件 |
|---|---|
| 2025-04 | 上线，靠"免绑卡 + 每月 $5"吸引大量个人用户 |
| 2026-04-23 | 发布停服公告 |
| 2026-05-11 | **服务正式停止**（公告后仅 18 天） |
| 2026-05-20 | 退款截止 |

从上线到关停不到 13 个月。更麻烦的是：**停服后它的宣传页依然挂得住、搜得到**，
只看页面不查公告，就会把一个已经死掉的服务当成可用选项。

所以：**用免费额度验证，用便宜的年付方案承载。**
一年 ¥38 的轻量服务器比"免费但可能下个月就没了"的额度更省心 ——
这不是价格比较，是风险比较。

> 好在本项目除 `alias.json` 外没有任何状态，换平台 = 改几个环境变量 +
> 拷一个文件。这个"说走就走"的能力是设计时刻意保留的，见
> [`DEPLOY_PAAS.md`](DEPLOY_PAAS.md) 第 0 节。

---

## 1. 为什么按量容器 PaaS 不划算

按量计费的定价逻辑是「**按实际运行时长付费**」，它的省钱前提是你**大部分
时间不在运行** —— 比如一个网站，半夜没人访问就可以缩容到 0，只付白天的钱。

但 QQ 官方机器人是 **WebSocket 长连接**：

- 连接一断，群里 @ 它就没人应答
- 平台的「缩容到 0」等于**把你的机器人下线**
- 所以你只能让它 24×7 常驻 → **按量计费永远按满月算**

结论：既然无论如何都要付 720 小时/月，那**包年**的轻量服务器必然更便宜。
拿 Sealos 举例，1核2G 常驻一个月约 ¥25.6，一年 ¥307 —— 而轻量服务器
38～99 元就能用一整年，配置还更高。

> **那什么情况下才该选按量容器？** 如果你的服务能接受"请求来了才启动"
> （比如一个 API、一个网站），按量确实更省。本项目不属于这一类。

---

## 2. 备案：不需要

这是本项目能安心用国内平台的关键前提。腾讯云官方文档写得很明确：

> **只购买云服务器不使用域名，需要备案吗？**
> 当您不使用域名解析指向服务器时，不需要进行备案。
>
> 若域名不解析到腾讯云中国境内云资源，仅通过服务器公网 IP 直接访问测试，
> 可暂不备案。
>
> —— <https://cloud.tencent.com/document/product/243/19630>

本机器人的情况：

| 备案判定项 | 本项目 |
|---|---|
| 是否绑定域名 | ❌ 不绑 |
| 是否对外提供网站/APP 访问 | ❌ 不提供 |
| 是否需要公网入站 | ❌ **完全不需要**（botpy 只向外连 `api.sgroup.qq.com`） |
| **结论** | **无需备案** |

机器人**不监听任何端口**（健康检查端口是可选的，且只用于平台内部探活，
不需要对外暴露），所以不构成"网站/APP 服务"。

> ⚠️ 但**实名认证是必须的**（这是账号层面的要求，和备案是两回事）。
> 个人实名即可，几分钟完成。

---

## 3. 首选方案：腾讯云轻量应用服务器

### 3.1 为什么选它

1. **网络最优** —— 腾讯云和 `api.sgroup.qq.com` 同在腾讯内网骨干，
   长连接的稳定性和延迟都最好（这是 QQ 官方机器人，用腾讯云天然对口）
2. **便宜** —— 首年 ¥38，之后 ¥99/年
3. **免费试用 1 个月** —— 可以零成本接着你现在的本地验证往下做
4. **无备案** —— 见上一节

### 3.2 怎么买最便宜

按身份区分，价格差很多（下表为 2026-09 公开活动价）：

| 活动 | 规格 | 价格 | 条件 |
|---|---|---|---|
| 🆓 免费试用 | 2核2G / 3M / 40G | **0 元 / 1 月** | 个人产品首单 |
| ⚡ 秒杀 | 4核4G / 3M / 40G | **¥38 / 1 年** | 个人专享 + 产品首单 |
| 🔥 服务器专区 | 2核2G / 3M / 40G | ¥68 / 1 年 | 产品首单 |
| 🔥 服务器专区 | 2核4G / 6M / 70G | ¥528 / 3 年 | 产品首单 |
| 🔄 同价续费 | 2核2G / 4M / 50G | ¥99 / 1 年 | 新老同享，限 1 次 |
| 🎓 学生认证 | 2核2G / 4M / 40G | ¥25.92 起 | 在校学生 |

**注意两个坑**：

- **"产品首单"指该账号从未买过 Lighthouse**，不是"第一次参加活动"。
  哪怕你只买过 1 个月，首单资格就没了。所以**先想好配置再下单**。
- **"同价续费"限新购 1 次 + 续费 1 次**，第 3 年起恢复原价。
  想长期便宜，`528 元 / 3 年`那档更划算。

**推荐路径**：免费试用（0 元）→ 验证 → 秒杀 4核4G（¥38/年）。

### 3.3 部署步骤

买完拿到公网 IP 后，SSH 上去：

```bash
# 1. 装 Python 3.10（Ubuntu/Debian 示例）
sudo apt update && sudo apt install -y python3.10 python3.10-venv git

# 2. 拉代码
sudo mkdir -p /opt && cd /opt
sudo git clone https://github.com/byuexzero/liz_bot.git
sudo chown -R $USER:$USER /opt/liz_bot && cd /opt/liz_bot

# 3. 建虚拟环境并装依赖
python3.10 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 4. 准备数据目录（见第 5 节：为什么不要用仓库内的路径）
sudo mkdir -p /var/lib/liz_bot
sudo chown -R $USER:$USER /var/lib/liz_bot

# 5. 先手工跑一遍确认能连上
export QQ_BOT_APPID=你的AppID
export QQ_BOT_SECRET=你的AppSecret
export LIZ_DATA_DIR=/var/lib/liz_bot
.venv/bin/python run.py
```

看到 `机器人 xxx 已就绪！` 就说明通了，在群里发个 `/id 8` 试试。
`Ctrl+C` 停掉，接着配开机自启。

### 3.4 开机自启（systemd）

凭据**不要**写在 `Environment=` 里 —— 那对任何能执行 `systemctl show`
的用户都可见。用权限 600 的独立文件：

```bash
sudo tee /etc/liz-bot.env >/dev/null <<'EOF'
QQ_BOT_APPID=你的AppID
QQ_BOT_SECRET=你的AppSecret
LIZ_DATA_DIR=/var/lib/liz_bot
PYTHONUNBUFFERED=1
# 单元里 ProtectSystem=strict 会把 /opt 挂成只读，Python 写不了 __pycache__。
# 显式关掉字节码写入，避免每次启动都静默回退到重新解析源码。
PYTHONDONTWRITEBYTECODE=1
EOF
sudo chmod 600 /etc/liz-bot.env
```

仓库里已备好 unit 文件 `deploy/liz-bot.service`。**装之前要改两处**：
`User=`（改成你的用户名）和 `WorkingDirectory` / `ExecStart`（改成你的仓库路径）。

```bash
# 把 User 改成当前用户（若仓库不在 /opt/liz_bot，再手动改路径）
sed -i "s|^User=lizbot|User=$USER|" deploy/liz-bot.service
sed -i "s|/opt/liz_bot|$PWD|g"       deploy/liz-bot.service

sudo cp deploy/liz-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now liz-bot
sudo systemctl status liz-bot      # 看状态
sudo journalctl -u liz-bot -f      # 看实时日志
```

> 若 `systemctl status` 报 `Failed to determine user credentials` 之类，
> 说明 `User=` 写的用户名在这台机器上不存在 —— 改回你自己登录用的用户名即可。

`Restart=always` + `RestartSec=10` 让它崩溃后 10 秒自动拉起 —— 比容器平台
的手动重启省心。

> 单元里还开了 `ProtectSystem=strict` + `ReadWritePaths=/var/lib/liz_bot`：
> 除数据目录外整个文件系统对它只读。**如果以后改了 `LIZ_DATA_DIR`，
> 记得同步改 `ReadWritePaths`**，否则机器人会因写不进去而报错。

### 3.5 更新代码

```bash
cd /opt/liz_bot
git pull
.venv/bin/pip install -r requirements.txt   # 依赖有变时才需要
sudo systemctl restart liz-bot
```

---

## 4. 备选方案

### 4.1 阿里云 ECS 经济型 e 实例 —— ¥99/年，**新购续费同价**

配置 2核2G / 3M / 40G ESSD，**不用抢、新老用户都能买、续费不涨价**。

比腾讯云秒杀贵（¥99 vs ¥38），但胜在**省心**：不用蹲点抢购，
第二年也不会突然涨到几百。适合不想折腾活动的人。

> 阿里云轻量应用服务器另有 ¥38/年（2核2G，每日 10:00 与 15:00 限量秒杀）
> 和 ¥68/年（新用户保底价）。若抢不到，¥99 的 ECS 是更稳的选择。

### 4.2 Sealos —— 不想管系统的话

国内平台，按量计费，直接吃你的 `Dockerfile`，支持持久化卷，
不用碰 SSH 和 systemd。四个可用区（杭州阿里云 / 北京火山 / 广东腾讯云 / 新加坡谷歌）。

单价（广东腾讯云区，元/小时）：

| 项目 | 单价 | 我们的用量 | 月成本 |
|---|---|---|---|
| CPU | 0.017420 / 核 | 0.5 核 | ¥6.27 |
| 内存 | 0.008786 / GB | 1 GB | ¥6.33 |
| 存储卷 | 0.000532 / GB | 1 GB | ¥0.38 |
| | | **合计** | **≈¥12.98/月** |

一年约 ¥156 —— 比轻量服务器贵，但省掉了运维。**如果只是想验证，
它的按量特性反而友好：验证完就删，只付几块钱。**

> 1核2G 配置约 ¥25.6/月。给这个机器人 0.5核1G 就够用了。

### 4.3 不推荐：CloudBase 云托管 / SAE 等按量容器

腾讯云 CloudBase 云托管官方计费示例里，1核2G 常驻一个月 = 720 CCU；
按资源包单价（≈¥0.20/CCU）折算 **≈¥144～152/月**，一年 ¥1700 以上。

贵 40 倍，能力上却没有额外收益 —— 因为按量计费的优势（缩容到 0）你用不上。

---

## 5. ⚠️ VPS 上的一个坑：`alias.json` 会让 `git pull` 冲突

**这是从容器平台迁到 VPS 时最容易踩的坑，务必看。**

别名表默认写在 `liz_bot/maimaiDX_songs/alias.json`，而这个文件
**是被 git 跟踪的**（随仓库分发的基线）。

在容器平台上这不是问题 —— 你会设 `LIZ_DATA_DIR` 指向卷。
但 VPS 上「整个磁盘都是持久的」，很容易觉得没必要设，
于是机器人持续往这个**被跟踪的文件**里写用户新增的别名：

```
$ git pull
error: Your local changes to the following files would be overwritten by merge:
        liz_bot/maimaiDX_songs/alias.json
```

**修法：VPS 上也照样设 `LIZ_DATA_DIR`**，把可写数据挪到仓库外面：

```bash
export LIZ_DATA_DIR=/var/lib/liz_bot
```

这样 `alias.json`、`ai_chat/`、`bot_log/` 都落在 `/var/lib/liz_bot`，
仓库始终保持干净，`git pull` 永远不会冲突。

> 这正是当初设计 `LIZ_DATA_DIR` 时想要的抽象 —— 它解决的**不只是**
> "容器文件系统是临时的"，而是"**可写数据不该和代码混在一起**"。
> VPS 场景下这条理由依然成立。

---

## 6. 迁移清单

从本地/容器迁到 VPS：

- [ ] 设 `LIZ_DATA_DIR=/var/lib/liz_bot`（第 5 节）
- [ ] 凭据放 `/etc/liz-bot.env`，权限 `600`（不要用 `Environment=`）
- [ ] 确认机器人**只在一处运行**（QQ 机器人不能两处同时在线）
- [ ] 如果之前跑过容器版，把卷里的 `alias.json` 拷过来，否则别名会"消失"
- [ ] `systemctl enable` 确保开机自启
- [ ] 群里发 `/id 8` 确认在线

---

## 附：`Dockerfile` 还要吗？

要。它和本方案不冲突：

- 用 Sealos 这类容器平台 → 直接吃 `Dockerfile`
- 用轻量服务器 → 可以不用 Docker（直接 venv + systemd，更轻量），
  也可以 `docker run` 跑同一个镜像

镜像里的 `LIZ_DATA_DIR=/data` 与 VPS 上的 `/var/lib/liz_bot` 只是路径不同，
逻辑完全一致。**在本地用 `local_rehearsal.py` 验证过的结论，两边都适用。**
