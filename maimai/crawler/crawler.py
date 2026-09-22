"""批量抓取驱动。

移植自 sdgb ``getuserdata/crawler.py``。

从 ``preview.db`` 的 ``preview`` 表读出全部 userId，
用线程池逐个调用 :func:`maimai.crawler.updatedata.updatedata`，
带 tqdm 进度条，失败的 userId 记入日志文件便于重跑。

用法::

    python -m maimai.crawler.crawler --preview-db ./preview.db --output-db ./userdata.db
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Sequence

from tqdm import tqdm

from .updatedata import updatedata

__all__ = ["main", "get_user_ids", "TqdmLoggingHandler"]

logger = logging.getLogger(__name__)


class TqdmLoggingHandler(logging.Handler):
    """把日志写进 tqdm 进度条，避免打断进度条渲染。"""

    def __init__(self) -> None:
        super().__init__()
        self.pbar: tqdm | None = None

    def set_pbar(self, pbar: tqdm) -> None:
        self.pbar = pbar

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            if self.pbar is not None:
                self.pbar.write(message)
            else:
                print(message)
        except Exception:
            pass


def get_user_ids(preview_db_path: str) -> list[int]:
    """从 ``preview`` 表读出全部去重后的 userId（升序）。"""
    conn = sqlite3.connect(preview_db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT userId FROM preview ORDER BY userId")
        return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def _process_user(
    user_id: int,
    timestamp: int,
    db_path: str,
    max_retries: int,
    failed_log: Path,
) -> bool:
    """处理单个 userId。失败时记入 ``failed_log`` 并返回 ``False``。"""
    try:
        return updatedata(user_id, timestamp, db_path, max_retries)
    except Exception as exc:
        logger.error("userId %s 处理失败：%s", user_id, exc)
        with failed_log.open("a", encoding="utf-8") as handle:
            handle.write(f"{user_id}\n")
        return False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="从 preview.db 批量抓取用户详细数据"
    )
    parser.add_argument("--preview-db", default="./preview.db", help="preview.db 路径")
    parser.add_argument("--output-db", default="./userdata.db", help="输出数据库路径")
    parser.add_argument("--max-retries", type=int, default=3, help="接口重试次数")
    parser.add_argument(
        "--start-from", type=int, default=None, help="从该 userId 开始（含）"
    )
    parser.add_argument("--threads", type=int, default=4, help="线程数")
    parser.add_argument(
        "--failed-log", default="failed_userids.log", help="失败 userId 记录文件"
    )
    args = parser.parse_args(argv)

    handler = TqdmLoggingHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s")
    )
    for name in ("crawler", "updatedata", "maimai"):
        target = logging.getLogger(name)
        target.handlers = [handler]
        target.propagate = False
        target.setLevel(logging.INFO)

    user_ids = get_user_ids(args.preview_db)
    if args.start_from:
        user_ids = [uid for uid in user_ids if uid >= args.start_from]

    if not user_ids:
        logger.info("没有需要处理的 userId。")
        return 0

    logger.info("开始处理 %d 个 userId，线程数 %d", len(user_ids), args.threads)
    timestamp = int(time.time())
    failed_log = Path(args.failed_log)
    succeeded = 0

    with tqdm(total=len(user_ids), desc="处理用户") as pbar:
        handler.set_pbar(pbar)
        executor = ThreadPoolExecutor(max_workers=args.threads)
        try:
            futures = {
                executor.submit(
                    _process_user,
                    uid,
                    timestamp,
                    args.output_db,
                    args.max_retries,
                    failed_log,
                ): uid
                for uid in user_ids
            }
            for future in as_completed(futures):
                uid = futures[future]
                try:
                    if future.result():
                        succeeded += 1
                        pbar.set_postfix(current=f"{uid}")
                        logger.info("userId %s 处理成功", uid)
                    else:
                        pbar.set_postfix(current=f"{uid}")
                        logger.warning("userId %s 处理失败", uid)
                except Exception as exc:
                    logger.error("userId %s 出现未预期错误：%s", uid, exc)
                pbar.update(1)
        except KeyboardInterrupt:
            logger.info("收到中断信号，正在停止…")
            executor.shutdown(wait=False, cancel_futures=True)
            return 130
        finally:
            executor.shutdown(wait=True)

    logger.info("完成：成功 %d / 共 %d", succeeded, len(user_ids))
    if succeeded < len(user_ids):
        logger.info("失败的 userId 已记入 %s", failed_log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
