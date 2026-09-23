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

### 0.1 入门型够不够？—— 实测推算

**结论：够，而且余量在 20 倍以上。入门型（2核2G / 4M / 50G SSD / 300G 流量）
不是"勉强能跑"，是"根本用不满"。**

原因很简单：这个机器人的资源画像**不是"一台服务器"，是"一个常驻的聊天客户端"**。
它没有数据库、没有 HTTP 服务、没有定时批处理，99% 的时间在等 WebSocket 消息。

| 资源 | 入门型提供 | 实测需求 | 余量 |
|---|---|---|---|
| CPU | 2 核 | 单线程 asyncio；查一次歌 **0.03 s**，其余时间全在等网络 | 100× 以上 |
| 内存 | 2 GB | 峰值 **47.5 MB** | ≈ 40× |
| 系统盘 | 50 GB SSD | 镜像 ≈ 165 MB + 日志 ≈ 0.5 MB | 300× 以上 |
| 月流量 | 300 GB（**只算出流量**） | 文本消息每月几 MB | 1000× 以上 |
| 带宽 | 4 Mbps | 只有首次构建镜像时会跑满 | 见下 |

> 上面按**入门型里最常见的那档**（2核2G / 4M / 50G SSD / 300G 流量 / ¥99/年）算。
> 入门型的带宽区间是 2–12 Mbps，买更低档也一样够用 —— 带宽只影响
> 首次构建镜像要多久，不影响日常收发消息。
> 各档配置见 §3.2 的表格。

**内存实测明细**（`_tools/measure_memory.py all`，Windows RSS，Linux 相当）：

| 阶段 | 当前 RSS |
|---|---|
| 解释器基线 | 19.0 MB |
| `+ import botpy` | 37.1 MB |
| `+ import aiohttp` | 37.1 MB |
| `+ import liz_bot.qqgroupbot`（`run.py` 的实际路径） | 38.0 MB |
| `+ 首次查歌`（加载 1394 首曲库并建索引） | 47.2 MB |
| **峰值** | **47.5 MB** |

反复查歌不再增长（连查 16 次后仍是 47.2 MB）—— 曲库是启动时一次性读入的，
没有按查询累积的状态。

**镜像体积明细**：

| 组成 | 大小 | 来源 |
|---|---|---|
| `python:3.10-slim` 基础镜像 | 45.0 MB（压缩）／≈ 120 MB（解压） | docker-library/repo-info，2026-09-19 |
| 依赖包 | 40.2 MB | site-packages 实测（Linux wheel 相当） |
| 应用代码与只读数据 | 2.8 MB | `docker build` 上下文实测 |
| **镜像合计** | **≈ 165 MB** | 加上构建缓存峰值也就 1 GB 出头 |

**日志**：botpy 的 `TimedRotatingFileHandler` 按天轮转、`backupCount: 7`
（`when: "D"`），所以磁盘占用**天然封顶**，不会随运行时长增长。
日志行实测约 100 字节/行；按个人机器人的量级（每天几百条消息）估算
约 **60 KB/天** —— 7 天稳态不到 0.5 MB，一年也就 20 MB 出头。
（这一项是估算不是实测：本机没有连续跑满一天的日志样本。）

#### 唯一值得想一想的：4 Mbps 带宽

**先说一个容易搞错的前提：腾讯云轻量的流量包只统计"出流量"。**
官方文档原文：

> 轻量应用服务器的套餐采用流量包模式，**流量包仅统计实例的出流量**。
>
> —— <https://cloud.tencent.com/document/product/1207/44368>

所以 `docker pull`、`apt-get`、`pip install` 这些**都不计流量**，
只受 4 Mbps 速率限制。会跑满带宽的只有两件事：

1. **首次构建镜像**：基础镜像 45 MB + apt ≈ 200 MB + pip ≈ 40 MB ≈ **300 MB 入站**，
   4 Mbps 下理论下限约 10 分钟。实际会更久 —— 因为瓶颈通常不是带宽而是
   `deb.debian.org` 的国内访问速度。解法见 [§3.6.3](#363-首次构建慢怎么办)。
2. **机器人往外发图片**（曲绘 / b50 图 / emoji）：单张几百 KB，4 Mbps 下不到 1 秒。

日常运行只有 WebSocket 心跳和文本消息，**每月几 MB 量级**。
300 GB/月是什么概念：按每张 300 KB 算，等于 100 万张图片 —— 用不到。

> 超额流量按 GB 另外计费，但以本项目的用量，这个风险等于零。

#### 什么情况下才需要换更大的套餐

| 情况 | 判断 |
|---|---|
| 就在这台机器上跑这一个机器人 | **入门型足够**，2G 里机器人只占 2.3% |
| 同一台机器还要跑别的服务（另一个机器人、数据库、面板） | 内存仍有余（1.7 GB 可用），但注意 2G 是**整机**额度 |
| 要用机器人做大批量图片/文件分发 | 换**锐驰型**（200 Mbps + 无限流量），瓶颈只在带宽 |

CPU 的余量大到不需要讨论 —— 就算入门型是共享型 CPU，
本项目的占用率也在 1% 以下，任何 CPU 规格都感知不到差异。

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

> 好在本项目没有任何需要长期保存的业务状态 —— 曲库随镜像分发且只读，
> 运行期只往卷上写日志与会话历史，换平台 = 改几个环境变量。
> 这个"说走就走"的能力是设计时刻意保留的，见
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

> 如果你已经决定买**入门型（2核2G / 4M / 50G SSD / ¥99/年）**：**够用，不用纠结。**
> 实测这个机器人峰值内存 47.5 MB、镜像约 165 MB、日志 60 KB/天量级，
> 对入门型来说余量在 20 倍以上（完整推算见 §0.1）。
> 4核4G 那档的意义不在于"跑得动"，而在于将来往这台机器上叠别的东西时不用再换。

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

### 3.6 用 Docker 跑（可选，与 3.3 / 3.4 二选一）

**3.3–3.5 和 3.6 是同一台机器上的两条路，选一条走就行。**
两个都跑会触发「同一个机器人两处同时上线」，QQ 侧会互相踢下线。

| | venv + systemd（3.3–3.5） | Docker（3.6） |
|---|---|---|
| 常驻开销 | 无额外进程 | Docker 守护进程约占 100 MB 内存 |
| 环境一致性 | 依赖宿主机 Python 版本 | 镜像内自带 Python 3.10，与宿主机无关 |
| 升级 | `git pull` + `pip install` | `git pull` + 重新构建镜像 |
| 换平台 | 要重装环境 | 镜像直接搬走 |
| 排查 | `journalctl -u liz-bot` | `docker compose logs` |

按 §0.1 的实测，2 GB 内存完全放得下 Docker 守护进程，**两条路都跑得动**。
想要"环境绝对可复现"就选 Docker。

#### 3.6.1 登服务器、装 Docker、配镜像加速

**① 拿到公网 IP 和用户名，SSH 上去。**

公网 IP 有三个来源，任选：

```bash
# 最可靠：腾讯云元数据服务。只允许从实例内部访问、走内网，
# 所以就算这台机器出不了公网（比如 GitHub 被卡）它照样能用。
curl -s http://metadata.tencentyun.com/latest/meta-data/public-ipv4; echo

# 也可以问外部服务（前提是能出公网）
curl -s https://api.ipify.org; echo

# 或者看控制台：轻量应用服务器 → 实例列表 → 点进实例 → 「概要」页
```

> ⚠️ **别用 `ip addr` / `ifconfig`** —— 那显示的是**内网 IP**（`10.x.x.x`）。
> 腾讯云的公网 IP 是 NAT 映射的，不在网卡上，拿它去 `scp` 连不上。
>
> 顺带：这台机器已经有公网 IP 才能被访问，但**IP 可能变**（除非绑了弹性 IP）。
> 以后连不上先回来查一遍。

用户名看镜像类型：实测腾讯云 Ubuntu 系统镜像是 **`ubuntu`**（不是 `root`），
应用镜像通常是 `lighthouse`。控制台「概要」页也写着。

```bash
ssh ubuntu@你的公网IP
```

**② 装 Docker。** 用官方脚本 + 国内镜像源，一步装齐 `docker-ce` +
`containerd` + **`docker compose`（v2 插件）**：

```bash
curl -fsSL https://get.docker.com | sh -s docker --mirror Aliyun
sudo systemctl enable --now docker
```

> `--mirror Aliyun` 让安装源指向 `mirrors.aliyun.com/docker-ce`；
> 不加的话走 `download.docker.com`，国内会很慢甚至超时。
> 这个脚本**会**一并装上 `docker-compose-plugin`，所以后面能直接用
> `docker compose`（带空格的新版命令，不是老的 `docker-compose`）。
>
> 若报 `Unsupported distribution`（镜像是 OpenCloudOS / TencentOS 等小众发行版时）：
> 改用发行版自带的包 —— `apt install -y docker.io docker-compose-v2`
> 或 `dnf install -y docker docker-compose-plugin`。
> 实在装不上 compose 插件也不要紧，跳到
> [§3.6.7](#367-不用-compose-的等价写法) 用 `docker run` 跑，效果一样。

**③ 把当前用户加进 `docker` 组（必做，否则每条命令都要 `sudo`）。**

装完 Docker 后，非 root 用户连不上 `/var/run/docker.sock`，任何 docker
命令（包括 `docker compose up` 去取镜像）都会报：

```
permission denied while trying to connect to the Docker daemon socket
at unix:///var/run/docker.sock
```

```bash
sudo usermod -aG docker "$USER"
id -nG "$USER"               # 应看到 "... ubuntu docker ..."，没有就是没加上
```

**然后必须断开重连** —— 已登录的 shell 不会自动拿到新组：

```bash
exit                         # 回到你本机
ssh ubuntu@你的公网IP        # 重新连
id -nG                       # 这次应该有 docker
docker info >/dev/null 2>&1 && echo "docker 可用"

docker version          # 有 Client 和 Server 两段才算装好
docker compose version  # 要能看到 v2.x，说明 compose 插件也在
```

> **`newgrp docker` 靠不住**：它在子 shell 里生效，换个终端就没了 ——
> VS Code 远程终端、tmux、screen 里经常失效，症状是"明明加过组还是
> permission denied"。**断开重连是最稳的**。
>
> 实在不想折腾也可以把后面所有 `docker` 写成 `sudo docker`，能跑通；
> 但 `sudo docker compose up` 建的 `deploy/data/` 会属 root，
> 以后备份、看日志都得再加 `sudo`。

**④ 配镜像加速（必做）。** 不配的话 `docker pull python:3.10-slim`
在国内大概率超时或慢到不可用：

```bash
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
    "registry-mirrors": ["https://mirror.ccs.tencentyun.com"]
}
EOF
sudo systemctl restart docker
docker info | grep -A2 "Registry Mirrors"   # 确认生效
```

> ⚠️ `mirror.ccs.tencentyun.com` **只支持腾讯云内网访问**，不支持外网域名访问
> —— 这是官方文档明确写的。也就是说它在 Lighthouse 上可用，
> 在你自己的电脑上不可用。别把它配到本机。

#### 3.6.2 拉代码、起容器

```bash
# 1. 拉代码（仓库很小：pack 仅 883 KB / 69 个文件，正常几秒完成）
#    若卡在 "Cloning into 'liz_bot'..." 不动，见 §3.6.9 的 scp 兜底方案
sudo mkdir -p /opt && cd /opt
sudo git clone https://github.com/byuexzero/liz_bot.git
sudo chown -R "$USER":"$USER" /opt/liz_bot   # sudo git clone 出来的文件属 root
cd /opt/liz_bot
git log --oneline -1                         # 确认是最新提交

# 2. 准备凭据（模板见 deploy/.env.example）
cp deploy/.env.example deploy/.env
nano deploy/.env                # 填 QQ_BOT_APPID / QQ_BOT_SECRET（没装 nano 就用 vim）
chmod 600 deploy/.env           # 同机其他用户不该读得到 AppSecret

# 3. 构建并启动
docker compose -f deploy/docker-compose.yml up -d --build

# 4. 看状态与日志
docker compose -f deploy/docker-compose.yml ps
docker compose -f deploy/docker-compose.yml logs -f
```

看到 `机器人 xxx 已就绪！` 就成了，群里发个 `/id 8` 试试。

> 若报 `docker: 'compose' is not a docker command`（只有老的独立二进制），
> 把 `docker compose` 换成 `docker-compose`，其余参数不变。

`deploy/.env` 已被 `.gitignore` 的 `.env` 规则排除，不会入库。
（仓库根目录那个 `.env` 是给 maimai/SDGB 工具链用的，两者互不相干。）

#### 3.6.3 首次构建慢怎么办

`Dockerfile` 里的 `apt-get install build-essential` 要从 Debian 官方源
拉约 **200 MB** 的 deb 包，国内直连可能几十分钟。

**办法一（推荐）：在 `deploy/.env` 里指定 apt 与 PyPI 镜像源。**

```bash
cat >> deploy/.env <<'EOF'
APT_MIRROR=mirrors.cloud.tencent.com
PIP_INDEX=https://mirrors.cloud.tencent.com/pypi/simple
EOF
docker compose -f deploy/docker-compose.yml up -d --build
```

> 已核对 `mirrors.cloud.tencent.com` 是**完整的 Debian 镜像**：
> `/debian`（bookworm、trixie）与 `/debian-security`
> （bookworm-security、trixie-security）的 `Release` 全部 200，
> 主归档的 `Packages.gz`/`Packages.xz` 也都在。
>
> ⚠️ **但 `/debian-security` 只发布 `Packages.xz`，没有 `Packages.gz`**
> （实测 trixie-security、bookworm-security 均为 `.xz` 200 / `.gz` 404）。
> 这是 Debian 安全归档的正常形态，apt 原生支持 xz —— **不是镜像残缺**。
> 手工探测时别拿 `.gz` 去试，否则会得出"镜像坏了"的错误结论。
>
> **为什么不每次敲 `--build-arg`**：`docker compose up -d --build` 会
> **不带**参数重新构建，把 `--build-arg` 的成果悄悄冲掉（踩过）。
> 写进 `.env` → compose 的 `build.args` 就不会漏。
>
> ⚠️ **别在任何地方写死发行版代号**：`python:3.10-slim` 的基础镜像
> 已经从 bookworm 换成 trixie（2026-09 核对），以后还会再变。

**办法二：干脆去掉 `build-essential`。** 先确认构建日志里全是
`Downloading ...whl`（**没有** `Building wheel`），然后把 `build-essential`
从 Dockerfile 的 `apt-get install` 里删掉 —— 那 200 MB 直接不用下了。
Dockerfile 的注释里本来就说可以删：13 个顶层依赖在 linux/amd64 上全有 wheel。

**办法三：临时用一次，不改 `.env`。**

```bash
docker compose -f deploy/docker-compose.yml build \
  --build-arg APT_MIRROR=mirrors.cloud.tencent.com \
  --build-arg PIP_INDEX=https://mirrors.cloud.tencent.com/pypi/simple
docker compose -f deploy/docker-compose.yml up -d    # ← 不要加 --build
```

> ⚠️ **`--build-arg` 只属于 `build`，`up` 不认识它。** 写成
> `docker compose up -d --build-arg ...` 会直接报 `unknown flag: --build-arg`
> 而**根本没开始构建**。必须像上面这样拆成两条命令。
>
> 另外注意最后一条**不要**加 `--build`：`up --build` 会不带参数重新构建，
> 把刚才 `--build-arg` 的成果冲掉，等于白等一次。（所以更推荐办法一，
> 写进 `.env` 就不会有这个问题。）

> 这三个办法都**只影响构建速度，不影响镜像内容** —— `APT_MIRROR` 只重写源的
> 主机名、不换发行版和组件；`PIP_INDEX` 只换下载来源，装什么版本仍由
> `requirements.txt` 的约束决定；`build-essential` 是装完即卸的编译环境。

如果换完源之后**还**慢，看构建日志卡在哪一段：

- 卡在 `Downloading <包名>...whl` → 瓶颈在 PyPI。**这是本机实测到的真实
  瓶颈，不是假想**：同一台服务器上，apt 换源后 82.5 MB 只用了 9 秒
  （8.8 MB/s），而直连 PyPI 下 770 kB 的 wheel 花了 21 秒（37 kB/s），
  475 kB 的 `pycryptodome` 索引页直接撞上 pip 默认的 15 秒读超时。
  用上面的 `PIP_INDEX` 即可（已核对本项目全部直接依赖在该镜像上都有；
  注意 PyPI 包名是 `qq-botpy`、`import` 名才是 `botpy`）。
- 卡在 `Building wheel for ...` → 说明某个包在源码编译，这时**不能**删
  `build-essential`，用办法一换 apt 源就够了。

> ⚠️ **一个很误导人的报错：`from versions: none` 不是"包不存在"。**
> PyPI 索引请求超时时，pip 报的是：
>
> ```
> ERROR: Could not find a version that satisfies the requirement pycryptodome>=3.23.0
> ERROR: No matching distribution found for pycryptodome>=3.23.0
> ```
>
> 看起来像这个包在 PyPI 上没有，**实际是网络挂了** —— 前面几个包明明
> 已经成功解析并下载了。**看到 `from versions: none` 先怀疑网络，别去改
> 包名或版本号**，否则会在一个不存在的问题上耗很久。

**关于镜像本身的可信度**（已实测，不是看文档写的）：
`mirrors.cloud.tencent.com/pypi/simple` 的索引页里，wheel 链接是**相对路径**
（官方 PyPI 是指向 `files.pythonhosted.org` 的绝对路径），也就是**它自己托管
wheel 文件**，不是只代理索引、下载仍回源到慢主机。实测拉取
`pycryptodome-3.23.0-...manylinux_2_17_x86_64.whl`：3.7 MB/s，
**sha256 与官方完全一致**。所以换源不影响装出来的东西。

#### 3.6.4 compose 里几个关键设置

| 设置 | 值 | 为什么是这个值 |
|---|---|---|
| `mem_limit` | `256m` | 实测峰值 47.5 MB 的 5 倍余量。不设限的话，将来一次内存泄漏会先吃光整台 2G 机器，**连 Docker 守护进程都会被 OOM 拖死**，症状是整机 SSH 卡住，比"容器自己被杀掉重启"难查得多 |
| `logging` | json-file / `10m`×3 | Docker 的 json-file 日志驱动**默认不轮转**，会一直长到撑爆磁盘。botpy 在 import 时就给 root logger 挂了 StreamHandler，日志会进这里，所以这个上限不是可选项 |
| `restart` | `unless-stopped` | 崩了自动拉起；但你手动 `stop` 之后它不会自己又爬起来（排查问题时很重要，`always` 会） |
| `ports` | `127.0.0.1:8080:8080` | 只绑回环。机器人是纯出站 WebSocket 客户端，**不需要任何公网入站**，健康检查在容器内部自己 curl 自己 |
| `volumes` | `./data:/data` | 日志与会话历史的落点。用 bind mount 而非 named volume：出问题可以直接 `sudo ls /opt/liz_bot/deploy/data`，不用先 `docker cp` |
| `security_opt` | `no-new-privileges:true` | 容器内以 root 运行只是为了让卷可写（见 Dockerfile 注释），不需要再提权 |
| `healthcheck` | 探响应体里的 `"bot": "ready"` | 见下 |

**关于健康检查的两个坑：**

1. **`docker ps` 显示 `unhealthy` 时，容器不会被自动重启。**
   Docker 的 `restart` 策略**只看进程是否退出**，不看 healthy / unhealthy。
   想在 unhealthy 时自动重启，得再挂一个 autoheal 容器 —— 本项目用不上，
   botpy 自带断线重连，进程活着就说明连接在维护中。健康检查的价值是
   **给你一个一眼可见的状态指示**，不是自动运维。
2. **探的是响应体不是状态码。** healthz 永远返回 200（启动阶段返回非 200
   会被平台判为启动失败，反而制造问题），真正区分状态的是 body 里的
   `"bot": "starting"` / `"ready"`（见 `liz_bot/healthz.py`）。
   探针用 `python -c` 而不是 `curl` —— `python:3.10-slim` 里没有 curl。

在机器上手工看一眼状态：

```bash
curl 127.0.0.1:8080
# {"status": "ok", "bot": "ready", "uptime_seconds": 12345.6}
```

#### 3.6.5 更新

```bash
cd /opt/liz_bot
git pull
docker compose -f deploy/docker-compose.yml up -d --build
docker image prune -f          # 清掉被替换下来的旧镜像层，否则磁盘会慢慢攒
```

#### 3.6.6 安全组：一个端口都不用开

机器人是**纯出站的 WebSocket 客户端**，只主动连 `api.sgroup.qq.com`；
健康检查端口绑在 `127.0.0.1` 上，只在本机可见。

所以 Lighthouse 的防火墙 / 安全组**保持默认即可**（只留 SSH 的 22）。
既不用开 8080，也不用做任何端口转发 —— 顺带也就没有"暴露到公网被人扫"的问题。

#### 3.6.7 不用 compose 的等价写法

装不上 `docker compose` 插件时（老发行版、或只想跑一条命令），
用 `docker run` 也能达到完全一样的效果 —— 下面每条 `--flag` 都对应
compose 里的一个设置：

```bash
cd /opt/liz_bot
docker build -t liz-bot .

docker run -d \
  --name liz-bot \
  --restart unless-stopped \
  --env-file deploy/.env \
  -e TZ=Asia/Shanghai \
  -e LIZ_DATA_DIR=/data \
  -e HEALTHZ_PORT=8080 \
  -v "$PWD/deploy/data:/data" \
  -p 127.0.0.1:8080:8080 \
  --memory 256m \
  --log-driver json-file --log-opt max-size=10m --log-opt max-file=3 \
  --security-opt no-new-privileges:true \
  --health-cmd "python -c \"import json,urllib.request as u,sys; d=json.load(u.urlopen('http://127.0.0.1:8080/',timeout=3)); sys.exit(0 if d.get('bot')=='ready' else 1)\"" \
  --health-interval 60s --health-timeout 5s --health-retries 3 \
  --health-start-period 30s \
  liz-bot
```

| compose 里的写法 | 对应的 `docker run` 参数 |
|---|---|
| `restart: unless-stopped` | `--restart unless-stopped` |
| `env_file: ./.env` | `--env-file deploy/.env` |
| `volumes: ./data:/data` | `-v "$PWD/deploy/data:/data"` |
| `ports: 127.0.0.1:8080:8080` | `-p 127.0.0.1:8080:8080` |
| `mem_limit: 256m` | `--memory 256m` |
| `logging: json-file / 10m ×3` | `--log-driver json-file --log-opt max-size=10m --log-opt max-file=3` |
| `security_opt: no-new-privileges` | `--security-opt no-new-privileges:true` |
| `healthcheck:` | `--health-cmd` + `--health-interval/timeout/retries/start-period` |

常用操作：`docker logs -f liz-bot` / `docker restart liz-bot` /
`docker stop liz-bot` / 更新见 [§3.6.5](#365-更新)（把 `up -d --build`
换成 `docker build -t liz-bot . && docker restart liz-bot`）。

#### 3.6.8 跑不起来时先看这里

按出现频率排序，**先看 `docker compose logs` 再动手**：

| 现象 | 原因 | 怎么办 |
|---|---|---|
| `git clone` 停在 `Cloning into 'liz_bot'...` | 这台机器出不了 GitHub（不是慢 —— 才 883 KB） | 见 [§3.6.9](#369-git-clone-卡住时从本机-scp-过去)：从本机 `git bundle` + `scp` |
| `docker pull` 卡住 / `i/o timeout` | 没配镜像加速，或配了但不在腾讯云内网 | 回 [§3.6.1](#361-登服务器装-docker配镜像加速) 第 ④ 步；`docker info` 里要能看到 `Registry Mirrors` |
| `permission denied … /var/run/docker.sock` | 当前用户不在 `docker` 组，或加了组但没重连 | [§3.6.1](#361-登服务器装-docker配镜像加速) 第 ③ 步：`sudo usermod -aG docker "$USER"` 后**必须断开重连**（`newgrp` 靠不住）。自查：`id -nG` |
| 构建卡在 `Get:… deb.debian.org` | Debian 官方源在国内慢 | 加 `--build-arg APT_MIRROR=mirrors.cloud.tencent.com`，见 [§3.6.3](#363-首次构建慢怎么办) |
| 日志里 `获取token失败，请检查appid和secret` | 凭据错 / 没读到 | 确认 `deploy/.env` 里两个值都填了、没留引号、没多余空格；`docker compose config` 能打出实际生效的环境变量 |
| 容器起来又立刻退出，`docker ps -a` 显示 `Exited (1)` | 启动自检没过 | `docker compose logs --tail=50`。启动失败时**一定**有一行 `启动失败：<原因>`（配置缺失、回复文本缺失等），照那行改 |
| `STATUS` 长期 `health: starting` | 连不上 QQ，`on_ready` 没触发 | 先看日志有没有 token 错误；再确认这台机器能出网（`curl -sI https://api.sgroup.qq.com`） |
| 群里 @ 没反应，但容器 `healthy` | 机器人已在别处上线 | QQ 机器人**不能两处同时在线**，先停掉本地/其他平台的实例 |
| 磁盘被占满 | 没设日志上限 / 没清旧镜像 | `docker system df` 看占用；`docker image prune -f`；确认 compose 里 `logging` 那段没被删 |

> 手工看健康检查端点（在服务器上执行，不是在你本机）：
>
> ```bash
> curl 127.0.0.1:8080
> # {"status": "ok", "bot": "ready", "uptime_seconds": 12345.6}
> ```
>
> `"bot"` 是 `starting` 就说明进程活着但还没连上 QQ。

#### 3.6.9 `git clone` 卡住时：从本机 scp 过去

**症状**：停在 `Cloning into 'liz_bot'...` 长时间不动。

这基本不是"慢"，是**这台机器到 GitHub 的连接被卡住**了 —— 仓库的 pack
只有 **883 KB**（12 个 commit、69 个文件），正常几秒就该开始打印
`Receiving objects`。先判断到底是哪种：

```bash
timeout 12 curl -sI https://github.com 2>&1 | head -2
timeout 12 curl -sI https://codeload.github.com 2>&1 | head -2
```

两条都超时 → 再重试多少次都一样，得绕开 GitHub。

> **关键前提：4 Mbps 限的是出流量，scp 上传到服务器不受它影响。**
> 883 KB 的包秒传，这条路比等 GitHub 快得多。

```bash
# ① 在你本机（仓库目录里）打包 —— 只含 git 跟踪的文件，带完整历史
cd D:\projectsega
git bundle create liz_bot.bundle --all
git bundle verify liz_bot.bundle          # 应打印 "is okay"

# ② 传到服务器（在你本机执行，不是在服务器上）
scp liz_bot.bundle ubuntu@你的公网IP:/tmp/

# ③ 在服务器上从 bundle 克隆
cd /opt
sudo git clone /tmp/liz_bot.bundle liz_bot
sudo chown -R "$USER":"$USER" /opt/liz_bot
cd /opt/liz_bot
git remote set-url origin https://github.com/byuexzero/liz_bot.git
git remote -v                             # 确认 origin 已指回 GitHub
git log --oneline -1                      # 确认拿到最新提交
```

> `git bundle` 就是"把仓库打成单个文件"，clone 出来有**完整历史**，
> 唯一区别是 `origin` 一开始指向那个文件 —— 所以第 ③ 步必须 `set-url`
> 改回 GitHub，否则以后 `git pull` 会去拉那个本地文件。
> `*.bundle` 已在 `.gitignore` 里，不会被误提交。

**其他备选**，按推荐程度排序：

| 办法 | 说明 |
|---|---|
| **Gitee「从 GitHub 导入仓库」** | 官方功能、国内快、安全。导入后在服务器 clone Gitee 地址，再 `git remote set-url origin` 改回 GitHub |
| **codeload 下 tarball** | `curl -L -o liz_bot.tar.gz https://github.com/byuexzero/liz_bot/archive/refs/heads/main.tar.gz`。**没有 `.git`**，以后只能整包覆盖更新，`git pull` 用不了 |
| 第三方 GitHub 代理 | 能用但**不建议**：它们中间人你的流量，且这类站点存活期普遍很短。真要试请自行确认可信度 |

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

## 5. `LIZ_DATA_DIR`：曾经必设，现在只是建议

**2026-09-23 之前，这一节是个真正的坑；现在这个坑已经填掉了。**

旧版把用户新增的歌曲别名写在 `liz_bot/maimaiDX_songs/alias.json`，
而这个文件**是被 git 跟踪的**。VPS 上「整个磁盘都是持久的」，很容易觉得
没必要设 `LIZ_DATA_DIR`，于是机器人持续往这个**被跟踪的文件**里写别名：

```
$ git pull
error: Your local changes to the following files would be overwritten by merge:
        liz_bot/maimaiDX_songs/alias.json
```

**现在不会再有这个冲突了**：曲库与别名库都改为**只读**（曲库来自水鱼
`divingfish_songs/music_data.json`，别名来自柚子
`yuzuchan_aliases/aliases.json`，均随镜像分发），歌曲别名的**写入**功能
已停用，运行期**不再修改仓库内任何文件**。

设 `LIZ_DATA_DIR` 仍然**推荐**，但理由从"避免 `git pull` 冲突"
变成了"让仓库目录只含代码"：

```bash
export LIZ_DATA_DIR=/var/lib/liz_bot
```

这样 `ai_chat/`、`bot_log/` 都落在 `/var/lib/liz_bot`，
仓库不会被日志撑大，也便于单独备份。

> 这正是当初设计 `LIZ_DATA_DIR` 时想要的抽象 —— 它解决的**不只是**
> "容器文件系统是临时的"，而是"**可写数据不该和代码混在一起**"。
> VPS 场景下这条理由依然成立，只是从"必须"降级为"建议"。

---

## 6. 迁移清单

从本地/容器迁到 VPS。**两条路选一条**（见 §3.6 开头的对比表）：

**A. venv + systemd（§3.3–3.5）**

- [ ] 设 `LIZ_DATA_DIR=/var/lib/liz_bot`（第 5 节，建议项）
- [ ] 凭据放 `/etc/liz-bot.env`，权限 `600`（不要用 `Environment=`）
- [ ] 确认机器人**只在一处运行**（QQ 机器人不能两处同时在线）
- [ ] 如果之前跑过容器版，把卷里的 `ai_chat/`、`bot_log/` 拷过来（可选，仅为保留历史）
- [ ] `systemctl enable` 确保开机自启
- [ ] 群里发 `/id 8` 确认在线

**B. Docker + compose（§3.6）**

- [ ] 先配 Docker 镜像加速源（§3.6.1），否则拉不到 `python:3.10-slim`
- [ ] `cp deploy/.env.example deploy/.env` 并 `chmod 600`，填 AppID / AppSecret
- [ ] 确认机器人**只在一处运行**（同上）
- [ ] `docker compose -f deploy/docker-compose.yml up -d --build`
- [ ] `docker compose ... ps` 里 `STATUS` 显示 `healthy`
- [ ] 群里发 `/id 8` 确认在线

---

## 附：`Dockerfile` 还要吗？

要。它和本方案不冲突：

- 用 Sealos 这类容器平台 → 直接吃 `Dockerfile`
- 用轻量服务器 → 可以不用 Docker（直接 venv + systemd，更轻量），
  也可以走 `deploy/docker-compose.yml`（见 §3.6）

镜像里的 `LIZ_DATA_DIR=/data` 与 VPS 上的 `/var/lib/liz_bot` 只是路径不同，
逻辑完全一致。**在本地用 `local_rehearsal.py` 验证过的结论，两边都适用。**
