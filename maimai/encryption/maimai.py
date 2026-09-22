"""maimai 业务接口加密。

移植自 ``eaquira/sdgb/encrypt.py``，**算法未做任何改动**，
仅把密钥与混淆参数改由 :mod:`maimai.config` 提供。

数据流::

    明文 JSON ──zlib 压缩──▶ ──AES-CBC/PKCS7 加密──▶ 请求体
    响应体   ──AES-CBC 解密──▶ ──zlib 解压──▶ 明文 JSON

混淆名算法（三方实现共通的根基）::

    md5( api_name + "Maimai" + region + obfuscate_param )

中国版 ``region = "Chn"``，此时结果与旧实现的
``md5(api + "MaimaiChn" + ObfuscateParam)`` **逐字节相同**。
"""

from __future__ import annotations

import hashlib
import random
import zlib
from functools import lru_cache

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from .. import config

__all__ = [
    "AesPkcs7",
    "aes_pkcs7",
    "obfuscator",
    "api_hash",
    "get_hash_api",
    "calc_random",
    "CalcRandom",
    "pack",
    "unpack",
]


class AesPkcs7:
    """AES-CBC + PKCS7。密钥与 IV 均为 UTF-8 字符串。"""

    def __init__(self, key: str, iv: str) -> None:
        self.key = key.encode("utf-8")
        self.iv = iv.encode("utf-8")
        self.mode = AES.MODE_CBC

    def encrypt(self, content: bytes) -> bytes:
        cipher = AES.new(self.key, self.mode, self.iv)
        return cipher.encrypt(pad(content, AES.block_size))

    def decrypt(self, content: bytes) -> bytes:
        cipher = AES.new(self.key, self.mode, self.iv)
        return unpad(cipher.decrypt(content), AES.block_size)

    # 以下两个方法保留自旧实现。它们对 bytes 调用 ord()/str 拼接，
    # 实际不可用于字节串，仅为兼容历史调用点而保留。
    def pkcs7unpadding(self, text):
        length = len(text)
        unpadding = ord(text[length - 1])
        return text[0 : length - unpadding]

    def pkcs7padding(self, text):
        bs = 16
        length = len(text)
        bytes_length = len(text.encode("utf-8"))
        padding_size = length if (bytes_length == length) else bytes_length
        padding = bs - padding_size % bs
        padding_text = chr(padding) * padding
        return text + padding_text


#: 旧实现的类名，保留为别名
aes_pkcs7 = AesPkcs7


@lru_cache(maxsize=1)
def _cipher() -> AesPkcs7:
    """按需构造并缓存业务加密器（延迟到首次使用，避免 import 期副作用）。"""
    return AesPkcs7(config.MAIMAI_AES_KEY, config.MAIMAI_AES_IV)


def obfuscator(src_str: str | None) -> str:
    """等价于 C# 的 ``Packet.Obfuscator``。

    ``md5(UTF8((src or "") + obfuscate_param))`` 的小写十六进制串。
    """
    return hashlib.md5(
        ((src_str or "") + config.MAIMAI_OBFUSCATE_PARAM).encode("utf-8")
    ).hexdigest()


def api_hash(api: str) -> str:
    """接口名 → 混淆后的 URL 片段。

    会拼上区域后缀，因此同一个接口名在中国版与国际版得到不同的混淆名。
    """
    return obfuscator(f"{api}Maimai{config.MAIMAI_REGION}")


#: 旧实现的名字，保留为别名
get_hash_api = api_hash


def calc_random() -> int:
    """生成 ``playSpecial`` 字段所需的伪随机数。

    算法与旧实现完全一致：取 1..1037933 的随机数乘以 2069，
    加 1024 后按位反转 32 位。
    """
    num = random.randint(1, 1037933) * 2069
    num += 1024
    result = 0
    for _ in range(32):
        result <<= 1
        result += num % 2
        num >>= 1
    return result


#: 旧实现的名字，保留为别名
CalcRandom = calc_random


def pack(plaintext: str) -> bytes:
    """明文 JSON → zlib 压缩 → AES 加密。"""
    return _cipher().encrypt(zlib.compress(plaintext.encode("utf-8")))


def unpack(ciphertext: bytes) -> str:
    """AES 解密 → zlib 解压 → 明文 JSON。

    解密失败时按未加密数据处理（与旧实现行为一致）——服务器在部分
    错误响应中会直接返回明文。
    """
    try:
        decrypted = _cipher().decrypt(ciphertext)
    except Exception:
        decrypted = ciphertext
    return zlib.decompress(decrypted).decode("utf-8")
