"""加密层。

- :mod:`maimai.encryption.maimai` —— 业务接口（AES-CBC + zlib + MD5 混淆）
- :mod:`maimai.encryption.aime`   —— 基板接口（AES-CBC + 随机 IV）
"""

from . import aime, maimai

__all__ = ["aime", "maimai"]
