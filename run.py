"""项目入口 —— 加载配置并启动 QQ 群机器人。

配置来源优先级：**环境变量 > 本地 YAML**。

部署到云平台（容器平台或 VPS）时只需设置环境变量::

    QQ_BOT_APPID=<你的 AppID>
    QQ_BOT_SECRET=<你的 AppSecret>

仓库中不含 ``liz_bot/config/config.yaml``（已被 .gitignore 排除），
因此在云端 YAML 回退会自动跳过，不会报错。

容器平台额外建议
----------------
``LIZ_DATA_DIR``  指向挂载的持久化卷（如 ``/data``），否则重启会丢别名表；
                  详见 :mod:`liz_bot.runtime_paths`。
``HEALTHZ_PORT``  让容器监听一个端口，避免平台因「无监听端口」判为不健康；
                  详见 :mod:`liz_bot.healthz`。
"""

import os
import sys

from liz_bot import healthz, runtime_paths
from liz_bot import qqgroupbot
from liz_bot.config import ConfigError, load_bot_config

# 本地开发用的 YAML 回退路径（可选）。
# 仅当环境变量缺少 appid / secret 时才会被读取；文件不存在则自动忽略。
LOCAL_CONFIG_YAML = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "liz_bot", "config", "config.yaml",
)


def main() -> None:
    # 1. 先把数据目录准备好（含空卷播种），并把解析结果打进日志 ——
    #    云端排查「别名莫名消失」时，第一眼要能看出数据到底落在哪。
    for line in runtime_paths.describe():
        print(line)
    for note in runtime_paths.ensure_dirs():
        print(note)

    # 2. 校验配置。**刻意放在健康检查之前**：配置缺失时应当直接退出并留下
    #    清晰报错，而不是让容器看起来"健康"却干不了活。
    try:
        config = load_bot_config(LOCAL_CONFIG_YAML)
    except ConfigError as exc:
        # 打印简洁错误而不是完整回溯，方便在云平台日志里一眼看到原因
        print(f"启动失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from None

    # 3. 起健康检查端口（未设置 HEALTHZ_PORT / PORT 时为空操作）
    port = healthz.start()
    if port:
        print(f"健康检查已监听 0.0.0.0:{port}")

    qqgroupbot.run_bot(config)


if __name__ == "__main__":
    main()
