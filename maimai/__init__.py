"""maimai / SDGB 客户端。

以 **eaquira 的流程逻辑**为主干，融合 **Lionheart** 的基板加密、类型系统与容器层，
并把全部密钥与敏感参数外置到项目根目录的 ``.env``。

模块一览
--------
===========================  ==================================================
:mod:`maimai.config`         配置加载（.env）
:mod:`maimai.encryption`     加密层：业务接口 AES-CBC+zlib、基板接口随机 IV
:mod:`maimai.client`         业务接口客户端（重试 / 代理 / 连接池）
:mod:`maimai.qr`             A.I.M.E. 扫码接口
:mod:`maimai.payloads`       简单请求体构造
:mod:`maimai.user_data`      用户数据聚合（容器驱动的增量上传）
:mod:`maimai.workflow`       上号工作流
:mod:`maimai.containers`     容器层（成绩 / 角色 / 道具 / 任务，含脏跟踪）
:mod:`maimai.typings`        26 个接口的全量类型定义
:mod:`maimai.crawler`        批量数据抓取
===========================  ==================================================

注意：导入本包会加载 ``.env``；缺少必填项时会在导入期直接报错并指出缺哪一项。

快速上手::

    from maimai import config, MaimaiClient

    print(config.summary())
"""

from __future__ import annotations

from . import (
    config,
    containers,
    crawler,
    encryption,
    payloads,
    typings,
    user_data,
)
from .client import MaimaiApiError, MaimaiClient
from .qr import (
    ERROR_ID_BAD_PARAMS,
    ERROR_ID_EXPIRED,
    ERROR_ID_SUCCESS,
    QrApiError,
    parse_qr,
    qr_api,
    qr_api_async,
)
from .user_data import PlayInput, UserData
from .workflow import WorkflowResult, run_workflow

__version__ = "2.0.0"

__all__ = [
    "__version__",
    # 子模块
    "config",
    "encryption",
    "typings",
    "containers",
    "payloads",
    "user_data",
    "crawler",
    # 客户端
    "MaimaiClient",
    "MaimaiApiError",
    # 扫码
    "qr_api",
    "qr_api_async",
    "parse_qr",
    "QrApiError",
    "ERROR_ID_SUCCESS",
    "ERROR_ID_EXPIRED",
    "ERROR_ID_BAD_PARAMS",
    # 用户数据
    "UserData",
    "PlayInput",
    # 工作流
    "run_workflow",
    "WorkflowResult",
]
