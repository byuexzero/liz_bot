"""A.I.M.E. / allnet 基板接口加密。

移植自 Lionheart 的 ``src/encryption/aime.ts``。

与旧实现（``eaquira/sdgb/keychip.py``）的关键差异
--------------------------------------------------

旧实现用**固定全零 IV**，并把 16 个零字节作为首个明文分组：

    密文 = E_{IV=0}( 0¹⁶ ‖ 0¹⁶ ‖ payload )

本实现改用 Lionheart 的做法——**随机 IV 且以明文前置**：

    密文 = IV ‖ E_{IV}( payload )

两者解密时等价（CBC 的分组特性 ``P_i = D(C_i) ⊕ C_{i-1}`` 使然），
但固定零 IV 在密码学上是缺陷：相同明文会产生完全相同的密文，
可被用于指纹识别与重放分析。随机 IV 才是正确构造。

Lionheart 是经过实际验证的实现，本模块以其格式为准。
"""

from __future__ import annotations

import os

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from .. import config

__all__ = ["encode", "decode", "BLOCK_SIZE"]

#: AES 分组大小，同时也是前置 IV 的长度
BLOCK_SIZE = 16


def encode(data: str, key: bytes, iv: bytes | None = None) -> bytes:
    """加密基板请求体。

    :param data: 待发送的表单串，形如 ``title_id=SDGB&title_ver=1.52&...\\r\\n``
    :param key: 16 字节密钥
    :param iv: 可选 16 字节 IV；省略时使用 ``os.urandom`` 生成随机 IV
    :return: ``IV ‖ 密文``
    """
    if iv is None:
        iv = os.urandom(BLOCK_SIZE)
    if len(iv) != BLOCK_SIZE:
        raise ValueError(f"IV 必须为 {BLOCK_SIZE} 字节，实际 {len(iv)} 字节")
    if len(key) != BLOCK_SIZE:
        raise ValueError(f"密钥必须为 {BLOCK_SIZE} 字节，实际 {len(key)} 字节")

    cipher = AES.new(key, AES.MODE_CBC, iv)
    encrypted = cipher.encrypt(pad(data.encode("utf-8"), AES.block_size))
    return iv + encrypted


def decode(data: bytes, key: bytes) -> str:
    """解密基板响应体。

    取前 16 字节作为 IV，解密其余部分。与 Lionheart 一致，
    **不**额外丢弃分组。
    """
    if len(data) < BLOCK_SIZE:
        raise ValueError(f"响应数据过短（{len(data)} 字节），无法取 IV")
    if len(key) != BLOCK_SIZE:
        raise ValueError(f"密钥必须为 {BLOCK_SIZE} 字节，实际 {len(key)} 字节")

    iv, ciphertext = data[:BLOCK_SIZE], data[BLOCK_SIZE:]
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return unpad(cipher.decrypt(ciphertext), AES.block_size).decode("utf-8")


def default_key() -> bytes:
    """从配置读取基板密钥。"""
    return config.aime_aes_key()
