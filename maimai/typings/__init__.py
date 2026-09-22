"""类型定义。

- :mod:`maimai.typings.base` —— 基础结构体与枚举（UserDetail、UserOption、成绩评级等）
- :mod:`maimai.typings.api`  —— 26 个接口的 Request / Response

两者均由 ``_tools/convert_typings.py`` 从 Lionheart 的 TypeScript 定义生成，
**请勿手工编辑**；上游更新后重跑脚本即可。

:data:`API_TYPES` 把接口名映射到 ``(Request, Response)``，
等价于 Lionheart ``src/typings/api/index.ts`` 里的 ``Api`` 类型表::

    from maimai.typings import API_TYPES

    req_cls, resp_cls = API_TYPES["GetUserPreviewApi"]
"""

from __future__ import annotations

from . import api as _api
from . import base as _base
from .api import *  # noqa: F401,F403
from .base import *  # noqa: F401,F403

#: 接口名 → (Request, Response)。等价于 Lionheart 的 ``Api`` 类型表。
#: 从生成的模块自动汇总，因此始终与 ``api.py`` 保持同步。
API_TYPES: dict[str, tuple[type, type]] = {}

for _name in dir(_api):
    if not _name.endswith("ApiRequest"):
        continue
    _stem = _name[: -len("Request")]
    _response = getattr(_api, f"{_stem}Response", None)
    if _response is not None:
        API_TYPES[_stem] = (getattr(_api, _name), _response)

del _name, _stem, _response, _api, _base

__all__ = ["API_TYPES"]
