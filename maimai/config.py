"""集中配置。

所有密钥、凭据、机厅信息一律从项目根目录的 ``.env`` 读取，
代码内不保留任何明文敏感值。

``.env`` 已被 ``.gitignore`` 排除；模板见 ``.env.example``。
"""

from __future__ import annotations

import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = _PROJECT_ROOT / ".env"


def _load_dotenv(path: Path) -> None:
    """加载 .env。

    优先使用 ``python-dotenv``；未安装时退回内置的极简解析器，
    保证本模块零强制依赖。两种方式都不覆盖已存在的真实环境变量。
    """
    if not path.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(path, override=False)
        return
    except ImportError:
        pass

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_dotenv(ENV_PATH)


def _req(name: str) -> str:
    """读取必填项，缺失时给出可操作的报错。"""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"缺少必填配置 {name}。请复制 .env.example 为 .env 并填写。\n"
            f"（期望位置：{ENV_PATH}）"
        )
    return value


def _opt(name: str, default: str = "") -> str:
    value = os.environ.get(name, "").strip()
    return value if value else default


def _opt_int(name: str, default: int = 0) -> int:
    raw = _opt(name)
    return default if not raw else int(raw)


# --------------------------------------------------------------------------
# maimai 业务接口加密参数
# --------------------------------------------------------------------------
MAIMAI_AES_KEY: str = _req("MAIMAI_AES_KEY")
MAIMAI_AES_IV: str = _req("MAIMAI_AES_IV")
MAIMAI_OBFUSCATE_PARAM: str = _req("MAIMAI_OBFUSCATE_PARAM")

#: 与 Mai-Encoding 请求头同值，随游戏版本变更
MAIMAI_ENCODING: str = _opt("MAIMAI_ENCODING", "1.53")

#: 服务器区域后缀。中国版 = Chn；国际版 = Exp。
#: 该值会拼进混淆名：md5(api + "Maimai" + REGION + OBFUSCATE_PARAM)
MAIMAI_REGION: str = _opt("MAIMAI_REGION", "Chn")


# --------------------------------------------------------------------------
# 基板 / 机台
# --------------------------------------------------------------------------
CHIP_ID: str = _req("CHIP_ID")
CLIENT_ID: str = _req("CLIENT_ID")

#: 游戏代号，用于 allnet 握手与 lastGameId
CODENAME: str = _opt("CODENAME", "SDGB")

#: allnet 握手中声明的版本号
TITLE_VER: str = _opt("TITLE_VER", "1.52")


# --------------------------------------------------------------------------
# 服务端点
# --------------------------------------------------------------------------
MAIMAI_BASE_URL: str = _opt(
    "MAIMAI_BASE_URL", "https://maimai-gm.wahlap.com:42081/Maimai2Servlet/"
)
ALLNET_BASE_URL: str = _opt("ALLNET_BASE_URL", "http://at.sys-allnet.cn")
AIME_QR_URL: str = _opt(
    "AIME_QR_URL", "http://ai.sys-allnet.cn/wc_aime/api/get_data"
)

#: A.I.M.E. 二维码接口的签名盐值
AIME_SALT: str = _req("AIME_SALT")

#: A.I.M.E. 基板握手用的 AES 密钥，以十六进制表示（16 字节 = 32 个 hex 字符）。
#: 用 hex 而非明文，是因为该密钥含引号和反斜杠，直接写进 .env 易被误解析。
AIME_AES_KEY_HEX: str = _req("AIME_AES_KEY_HEX")


def aime_aes_key() -> bytes:
    """A.I.M.E. 握手密钥的字节形式。"""
    try:
        key = bytes.fromhex(AIME_AES_KEY_HEX)
    except ValueError as exc:
        raise RuntimeError(
            f"AIME_AES_KEY_HEX 不是合法十六进制：{AIME_AES_KEY_HEX!r}"
        ) from exc
    if len(key) != 16:
        raise RuntimeError(
            f"AIME_AES_KEY_HEX 必须解出 16 字节，实际 {len(key)} 字节"
        )
    return key


# --------------------------------------------------------------------------
# 账号
# --------------------------------------------------------------------------
#: 舞萌 DX userId（8 位正整数）。读取时不强制校验，交给 client 层统一检查。
USER_ID: int = _opt_int("USER_ID")

#: 扫卡二维码。**这是可直接上号的凭据，等同于账号密码。**
QR_CODE: str = _opt("QR_CODE")


# --------------------------------------------------------------------------
# 机厅信息
# --------------------------------------------------------------------------
REGION_ID: int = _opt_int("REGION_ID", 1)
REGION_NAME: str = _opt("REGION_NAME", "")
PLACE_ID: int = _opt_int("PLACE_ID")
PLACE_NAME: str = _opt("PLACE_NAME", "")


# --------------------------------------------------------------------------
# 网络
# --------------------------------------------------------------------------
#: 可选代理，留空则直连。支持 http:// 与 socks5:// 两种协议。
#: 形如 socks5://127.0.0.1:1100
PROXY_URL: str = _opt("PROXY_URL")

#: 单次请求超时（秒）
REQUEST_TIMEOUT: float = float(_opt("REQUEST_TIMEOUT", "10"))

#: 失败重试次数（不含首次）
REQUEST_RETRIES: int = _opt_int("REQUEST_RETRIES", 2)

#: 是否跳过 TLS 校验。仅调试用，生产应保持 false。
INSECURE_TLS: bool = _opt("INSECURE_TLS", "false").lower() in ("1", "true", "yes")


# --------------------------------------------------------------------------
# 诊断
# --------------------------------------------------------------------------
def summary() -> dict[str, object]:
    """返回脱敏后的配置概览，便于排查问题。"""

    def mask(value: str) -> str:
        if not value:
            return "(未设置)"
        if len(value) <= 8:
            return "*" * len(value)
        return f"{value[:4]}…{value[-4:]}"

    return {
        "env_path": str(ENV_PATH),
        "env_exists": ENV_PATH.exists(),
        "MAIMAI_ENCODING": MAIMAI_ENCODING,
        "MAIMAI_REGION": MAIMAI_REGION,
        "MAIMAI_AES_KEY": mask(MAIMAI_AES_KEY),
        "MAIMAI_AES_IV": mask(MAIMAI_AES_IV),
        "MAIMAI_OBFUSCATE_PARAM": MAIMAI_OBFUSCATE_PARAM,
        "CHIP_ID": mask(CHIP_ID),
        "CLIENT_ID": mask(CLIENT_ID),
        "USER_ID": USER_ID or "(未设置)",
        "QR_CODE": mask(QR_CODE),
        "PLACE_NAME": PLACE_NAME or "(未设置)",
        "PROXY_URL": PROXY_URL or "(直连)",
        "REQUEST_TIMEOUT": REQUEST_TIMEOUT,
        "REQUEST_RETRIES": REQUEST_RETRIES,
        "INSECURE_TLS": INSECURE_TLS,
    }
