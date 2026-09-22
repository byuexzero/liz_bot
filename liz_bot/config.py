"""配置加载 —— 以环境变量为准，配置对象沿调用链手动传入。

为什么改
--------
原先 ``qqgroupbot`` / ``pic_haddler`` / ``text`` / ``qqgroup-ai-bot`` 四个模块
都在**模块级**执行 ``read("config/config.yaml")``。这带来两个问题：

1. 文件缺失时 **import 直接崩溃**，云平台上无法用环境变量顶替；
2. 配置是隐式全局变量，任何模块都能随手取用，无法测试、无法替换。

现在改为：配置由 :func:`load_bot_config` / :func:`load_ai_config` 显式取得，
再沿调用链**手动传入** —— ``run_bot(config)``、
``upload_file_by_index(..., config)``、``MyClient(..., bot_config=...)``。

环境变量
--------
=========================  ==========================================
``QQ_BOT_APPID``           机器人 AppID（必填）
``QQ_BOT_SECRET``          机器人 AppSecret（必填）
``QIANFAN_ACCESS_KEY``     千帆 AI Access Key（可选，仅 AI 变体用）
``QIANFAN_SECRET_KEY``     千帆 AI Secret Key（可选，仅 AI 变体用）
=========================  ==========================================

取值优先级：**环境变量 > 显式传入的 YAML**。YAML 只用于补齐环境变量中
缺失的字段，且必须由调用方显式给出路径（不再自动探测），文件不存在时
静默跳过，不会掩盖"环境变量缺失"这个真正的错误。

本地开发若想继续用 YAML，显式传路径即可::

    from liz_bot.config import load_bot_config
    config = load_bot_config("liz_bot/config/config.yaml")
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(RuntimeError):
    """配置缺失或不可用。"""


def _env(name: str) -> str:
    """读取环境变量并去掉首尾空白，未设置时返回空串。"""
    return (os.environ.get(name) or "").strip()


def _read_yaml(path: str | os.PathLike[str]) -> Mapping[str, Any]:
    """读取一个 YAML 键值文件。仅在调用方显式给出路径时才会被调用。"""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - PyYAML 是硬依赖
        raise ConfigError("读取 YAML 需要 PyYAML，请先 pip install PyYAML") from exc

    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except OSError as exc:
        raise ConfigError(f"配置文件读取失败：{path}（{exc}）") from exc

    if data is None:
        return {}
    if not isinstance(data, Mapping):
        raise ConfigError(f"配置文件格式无效，应为键值映射：{path}")
    return data


def _fill_from_yaml(
    values: dict[str, str], yaml_path: str | os.PathLike[str] | None
) -> None:
    """用 YAML 补齐 ``values`` 中的空字段（就地修改）。

    ``yaml_path`` 为 None 或文件不存在时什么都不做 —— 文件不存在不应报错，
    因为环境变量可能已经提供了全部必填项。
    """
    if yaml_path is None:
        return
    path = Path(yaml_path)
    if not path.is_file():
        return

    data = _read_yaml(path)
    for key in values:
        if values[key]:
            continue
        raw = data.get(key)
        if raw is not None:
            values[key] = str(raw).strip()


@dataclass(frozen=True, repr=False)
class BotConfig:
    """QQ 机器人凭据。"""

    appid: str
    secret: str

    def __repr__(self) -> str:
        # 刻意隐藏 secret，避免凭据经日志/异常回溯泄漏
        return f"BotConfig(appid={self.appid!r}, secret=***)"


@dataclass(frozen=True, repr=False)
class AIConfig:
    """千帆（文心）AI 凭据。两项都允许为空 —— 为空时 AI 功能不可用，
    但机器人本身仍能启动。"""

    access_key: str
    secret_key: str

    def __repr__(self) -> str:
        access = "***" if self.access_key else "<empty>"
        secret = "***" if self.secret_key else "<empty>"
        return f"AIConfig(access_key={access!r}, secret_key={secret!r})"


def load_bot_config(yaml_path: str | os.PathLike[str] | None = None) -> BotConfig:
    """取得机器人凭据。

    :param yaml_path: 可选的 YAML 回退路径。仅当环境变量缺少 ``appid`` 或
        ``secret`` 时才会被读取；文件不存在则忽略。
    :raises ConfigError: 环境变量与 YAML 都未能提供完整的 appid / secret。
    """
    values = {"appid": _env("QQ_BOT_APPID"), "secret": _env("QQ_BOT_SECRET")}
    _fill_from_yaml(values, yaml_path)

    missing = [name for name, val in values.items() if not val]
    if missing:
        env_names = {"appid": "QQ_BOT_APPID", "secret": "QQ_BOT_SECRET"}
        raise ConfigError(
            "缺少机器人配置：" + "、".join(env_names[m] for m in missing)
            + "。请设置这些环境变量"
            + ("，或在 yaml_path 指向的文件中提供对应字段。" if yaml_path else "。")
        )
    return BotConfig(appid=values["appid"], secret=values["secret"])


def load_ai_config(yaml_path: str | os.PathLike[str] | None = None) -> AIConfig:
    """取得千帆 AI 凭据。缺失时不报错，返回空值（AI 功能降级为不可用）。"""
    values = {
        "access_key": _env("QIANFAN_ACCESS_KEY"),
        "secret_key": _env("QIANFAN_SECRET_KEY"),
    }
    # YAML 里的键名是 ACCESS_KEY / SECRET_KEY，与内部键名不同，单独映射
    if yaml_path is not None and Path(yaml_path).is_file():
        data = _read_yaml(yaml_path)
        for key, yaml_key in (
            ("access_key", "ACCESS_KEY"),
            ("secret_key", "SECRET_KEY"),
        ):
            if not values[key] and data.get(yaml_key) is not None:
                values[key] = str(data[yaml_key]).strip()

    return AIConfig(access_key=values["access_key"], secret_key=values["secret_key"])
