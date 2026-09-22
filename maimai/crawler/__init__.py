"""批量数据抓取。

移植自 sdgb ``getuserdata/``：

- :mod:`maimai.crawler.updatedata` —— 单个 userId 的详细数据抓取与落库
- :mod:`maimai.crawler.crawler`   —— 线程池批量驱动 + tqdm 进度条

数据库表结构与旧版**完全一致**，已有 ``.db`` 文件可直接继续使用。

批量运行::

    python -m maimai.crawler.crawler --preview-db ./preview.db --output-db ./userdata.db
"""

from .updatedata import ensure_tables, updatedata

__all__ = ["updatedata", "ensure_tables"]
