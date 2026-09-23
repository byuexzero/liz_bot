# syntax=docker/dockerfile:1

# =============================================================================
# 舞萌 DX QQ 群机器人 —— 生产镜像
#
# 构建：
#     docker build -t liz-bot .
# 运行（本地，不挂卷、不开健康检查端口）：
#     docker run --rm -e QQ_BOT_APPID=xxx -e QQ_BOT_SECRET=yyy liz-bot
# 运行（模拟线上：挂持久化卷 + 监听健康检查端口）：
#     docker run --rm -v liz-data:/data -e LIZ_DATA_DIR=/data -e HEALTHZ_PORT=8080 \
#                -e QQ_BOT_APPID=xxx -e QQ_BOT_SECRET=yyy -p 8080:8080 liz-bot
#
# 环境变量说明见 README.md；数据卷与健康检查的设计动机见
# liz_bot/runtime_paths.py 与 liz_bot/healthz.py 的模块文档。
# =============================================================================

# 固定在 3.10：qq-botpy 目前只在 3.10 上验证过。
# requirements.txt 标注的支持区间是 3.10–3.12，此处取已验证的下界。
FROM python:3.10-slim

# PYTHONUNBUFFERED        日志实时输出，否则容器日志会攒在缓冲区里看不到
# PYTHONDONTWRITEBYTECODE 不生成 .pyc，容器内没有意义还多占镜像层
# TZ                      让 botpy 日志用东八区时间，排查问题更直观
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 先只拷 requirements：依赖没变时这一层能命中缓存，改代码不必重装依赖。
#
# build-essential 是编译环境的保险，tzdata 让上面的 TZ 生效（slim 镜像不自带）。
# 两者都在同一层内装完即卸，不会留在最终镜像里。
#
# build-essential 到底需不需要？已用 _tools/check_wheels.py 核对过：
# 13 个顶层依赖在 linux/amd64 上**全部有预编译 wheel**
# （纯 Python / cp310-cp310 / cp37-abi3 稳定 ABI），理论上不会触发源码编译。
# 但仍然刻意保留 —— 传递依赖没有逐一核实，且个别包可能因 glibc 版本
# 回退到源码，那时候没有编译器构建就会失败，而报错信息很难指向真正原因。
# 想缩短构建时间：确认构建日志里全是 "Downloading ...whl" 之后即可删掉它。
COPY requirements.txt ./

# 构建期的 apt 源主机名。留空 = 沿用基础镜像自带的 Debian 官方源
# （deb.debian.org），行为与加这个参数之前完全一致。
#
# 为什么留个口子：build-essential 要从 apt 拉约 200MB 的 deb 包，
# 在国内直连 deb.debian.org 可能慢到几十分钟。在腾讯云轻量服务器上构建时：
#
#     docker build --build-arg APT_MIRROR=mirrors.cloud.tencent.com -t liz-bot .
#
# 已核对 mirrors.cloud.tencent.com 同时提供 /debian 与 /debian-security
# 两个路径，且 `deb.debian.org` 这个主机名在 bookworm 的两条源里都出现，
# 一次替换即可覆盖。腾讯云实例上更快的 `mirrors.tencentyun.com` 是内网版，
# 是否可用取决于实例所在网络。
#
# 只重写主机名、不换发行版/组件，所以镜像内容与默认构建一致 ——
# 这个参数影响的是下载速度，不是构建结果。
ARG APT_MIRROR=""
RUN if [ -n "$APT_MIRROR" ]; then \
      for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources; do \
        if [ -f "$f" ]; then sed -i "s|deb.debian.org|$APT_MIRROR|g" "$f"; fi; \
      done; \
    fi; \
    apt-get update \
 && apt-get install -y --no-install-recommends build-essential tzdata \
 && pip install --no-cache-dir -r requirements.txt \
 && apt-get purge -y --auto-remove build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY . .

# 数据卷挂载点。仅保证目录存在 —— 真正挂载后镜像内的内容会被卷覆盖。
# 若在平台上把卷挂到别的路径，记得同步设置 LIZ_DATA_DIR。
RUN mkdir -p /data

# 刻意以 root 运行：平台挂载的卷通常属 root，换成非 root 用户会写不进去，
# 而写不进去的后果是日志与会话历史静默丢失（见 liz_bot/runtime_paths.py）。
#
# 刻意不写 HEALTHCHECK：容器是否监听端口取决于 HEALTHZ_PORT 是否设置，
# 写死一个健康检查会在未启用该端口时把健康的容器判成不健康、反复重启。
# 平台自身的 TCP 探活已足够。
CMD ["python", "run.py"]
