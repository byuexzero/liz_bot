"""项目入口 —— 加载配置并启动 QQ 群机器人。

配置来源优先级：**环境变量 > 本地 YAML**。

部署到云平台（如 Render）时只需设置环境变量::

    QQ_BOT_APPID=<你的 AppID>
    QQ_BOT_SECRET=<你的 AppSecret>

仓库中不含 ``liz_bot/config/config.yaml``（已被 .gitignore 排除），
因此在云端 YAML 回退会自动跳过，不会报错。
"""

import os
import sys

from liz_bot import qqgroupbot
from liz_bot.config import ConfigError, load_bot_config

# 本地开发用的 YAML 回退路径（可选）。
# 仅当环境变量缺少 appid / secret 时才会被读取；文件不存在则自动忽略。
LOCAL_CONFIG_YAML = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "liz_bot", "config", "config.yaml",
)


def main() -> None:
    try:
        config = load_bot_config(LOCAL_CONFIG_YAML)
    except ConfigError as exc:
        # 打印简洁错误而不是完整回溯，方便在云平台日志里一眼看到原因
        print(f"启动失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from None
    qqgroupbot.run_bot(config)


if __name__ == "__main__":
    main()
