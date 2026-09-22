"""本地彩排 —— 用「云端的方式」在本地跑一遍机器人。

为什么不直接 ``python run.py``
------------------------------
直接跑当然能启动，但它**证明不了云端能跑**。云端与本地只有两处差异，
而这两处恰好都是「静默失败」的重灾区：

======================  ==========================================
``LIZ_DATA_DIR``        云端文件系统是临时的 → 别名表会无声消失
``HEALTHZ_PORT``        云端按「有没有监听端口」判活 → 可能反复重启
======================  ==========================================

本脚本把这两个变量按云端的取值设好再启动，于是本地这一遍就等于
**云端那一遍的预演**。跑通它，云端基本不会出意外。

用法
----
::

    python _tools/local_rehearsal.py                # 彩排（默认）
    python _tools/local_rehearsal.py --plain        # 最简，等价于 python run.py
    python _tools/local_rehearsal.py --volume D:\\lizvol
    python _tools/local_rehearsal.py --port 18734

``--volume`` 指向的目录会被**反复复用**：第二次启动时它会告诉你
"卷上已有别名表，未被覆盖" —— 这正是云端「用户别名不会丢」的本地证明。
所以**建议至少跑两次**。

退出：``Ctrl+C``。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUN_PY = REPO / "run.py"
CONFIG_YAML = REPO / "liz_bot" / "config" / "config.yaml"
DEFAULT_VOLUME = REPO / ".local_volume"
DEFAULT_PORT = 18734

_print_lock = threading.Lock()


def say(msg: str = "") -> None:
    with _print_lock:
        print(msg, flush=True)


def section(title: str) -> None:
    say()
    say("=" * 72)
    say(title)
    say("=" * 72)


# ---------------------------------------------------------------------------
# 启动前检查 —— 尽量把"启动后才发现"的问题提前到这里
# ---------------------------------------------------------------------------

def check_python() -> bool:
    major, minor = sys.version_info[:2]
    if major != 3 or not (10 <= minor <= 12):
        say(f"[!] Python {major}.{minor} 不在 3.10–3.12 范围内"
            f"（qq-botpy 的限制），可能启动失败")
        return False
    say(f"[OK] Python {major}.{minor}")
    return True


def check_deps() -> bool:
    """检查运行必需的依赖。缺了会在 import 阶段炸，提前说清楚更好。"""
    import importlib.util

    # import 名 -> pip 包名（两者不同，报错时要给对名字）
    required = {
        "botpy": "qq-botpy",
        "ijson": "ijson",
        "yaml": "PyYAML",
        "aiohttp": "aiohttp",
    }
    missing = [pip for mod, pip in required.items()
               if importlib.util.find_spec(mod) is None]
    if missing:
        say(f"[!] 缺少依赖：{', '.join(missing)}")
        say(f"    安装：{sys.executable} -m pip install {' '.join(missing)}")
        return False
    say(f"[OK] 依赖齐全（{len(required)} 个）")
    return True


def check_credentials() -> bool:
    """确认凭据能取到，但**绝不打印凭据本身**。"""
    appid = (os.environ.get("QQ_BOT_APPID") or "").strip()
    secret = (os.environ.get("QQ_BOT_SECRET") or "").strip()
    source = "环境变量"

    if not (appid and secret) and CONFIG_YAML.is_file():
        try:
            import yaml

            data = yaml.safe_load(CONFIG_YAML.read_text(encoding="utf-8")) or {}
            appid = appid or str(data.get("appid") or "").strip()
            secret = secret or str(data.get("secret") or "").strip()
            source = f"环境变量 + {CONFIG_YAML.relative_to(REPO)}"
        except Exception as exc:  # noqa: BLE001 - 只是预检，失败交给 run.py 报
            say(f"[!] 读取 {CONFIG_YAML.name} 失败：{exc}")

    if not (appid and secret):
        say("[!] 取不到机器人凭据")
        say("    请设置 QQ_BOT_APPID / QQ_BOT_SECRET，"
            f"或在 {CONFIG_YAML.relative_to(REPO)} 里填写 appid / secret")
        return False

    say(f"[OK] 凭据已取到（来源：{source}）"
        f"  appid={appid}  secret=<{len(secret)} 字符，已隐去>")
    return True


def check_port(port: int) -> bool:
    """端口是否空闲。被占用时健康检查会起不来，但机器人本身照跑 ——
    这种"部分成功"最难发现，所以提前报出来。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
        except OSError:
            say(f"[!] 端口 {port} 已被占用 —— 健康检查将无法监听")
            say("    换一个：--port <其它端口>")
            return False
    say(f"[OK] 端口 {port} 空闲")
    return True


# ---------------------------------------------------------------------------
# 数据卷
# ---------------------------------------------------------------------------

def prepare_volume(volume: Path) -> None:
    """建卷目录，并判断这是首次还是再次启动。"""
    volume.mkdir(parents=True, exist_ok=True)
    alias = volume / "alias.json"
    if alias.is_file():
        size = alias.stat().st_size
        say(f"[OK] 数据卷 {volume}")
        say(f"     卷上已有 alias.json（{size} 字节）→ 本次**不会**覆盖它")
        say("     这就是云端「用户别名不会丢」的本地证明")
    else:
        say(f"[OK] 数据卷 {volume}（空卷，本次会播种基线别名表）")


# ---------------------------------------------------------------------------
# 健康检查轮询 —— 顺带当作"机器人是否连上 QQ"的信号
# ---------------------------------------------------------------------------

class HealthWatch:
    """轮询 /healthz，把状态变化打印出来。

    ``bot`` 字段由 ``on_ready`` 置为 ``ready``，而 ``on_ready`` 只在
    WebSocket 连上腾讯服务器后才会触发 —— 所以它同时也是
    "凭据有效 + 网络可达"的证据。
    """

    def __init__(self, port: int) -> None:
        self.port = port
        self.url = f"http://127.0.0.1:{port}/"
        self.last: str | None = None
        self.reached = False
        self.stop = threading.Event()

    def _once(self) -> dict | None:
        try:
            with urllib.request.urlopen(self.url, timeout=3) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def run(self) -> None:
        while not self.stop.is_set():
            data = self._once()
            if data is not None:
                if not self.reached:
                    self.reached = True
                    say(f"[OK] 健康检查端点已响应：{self.url}")
                status = data.get("bot")
                if status != self.last:
                    self.last = status
                    if status == "ready":
                        say("[OK] 机器人已连接 QQ（on_ready 已触发）")
                    elif status == "starting":
                        say("[..] 健康检查已就绪，等待连接 QQ …")
            self.stop.wait(1.0)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="本地彩排：用云端的方式跑一遍机器人",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--plain", action="store_true",
                    help="不设置任何环境变量，等价于直接 python run.py")
    ap.add_argument("--volume", default=None,
                    help=f"用作数据卷的目录（默认 {DEFAULT_VOLUME}）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help=f"健康检查端口（默认 {DEFAULT_PORT}）")
    ap.add_argument("--no-healthz", action="store_true",
                    help="不启用健康检查端口")
    args = ap.parse_args()

    section("启动前检查")
    ok = check_python()
    ok = check_deps() and ok
    ok = check_credentials() and ok
    if not ok:
        say()
        say("预检未通过，已中止。")
        return 1

    # ---- 组装子进程环境 -------------------------------------------------
    env = dict(os.environ)
    # 子进程 stdout 被管道接走时默认是**块缓冲**，而 botpy 的 logging 走
    # stderr（无缓冲）。两者混在一起会出现「错误先于启动信息」的错乱顺序，
    # 让人误判失败发生在启动之前。置 1 强制行缓冲。
    # （Dockerfile 里也设了这个，行为一致。）
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"   # 保证子进程中文输出不乱码

    watch: HealthWatch | None = None

    if args.plain:
        section("运行模式：最简（--plain）")
        say("不设置 LIZ_DATA_DIR / HEALTHZ_PORT，等价于直接 python run.py")
        say("注意：这一遍**不能**验证云端的两处差异")
    else:
        section("运行模式：彩排云端环境")
        volume = Path(args.volume).expanduser().resolve() if args.volume \
            else DEFAULT_VOLUME
        prepare_volume(volume)
        env["LIZ_DATA_DIR"] = str(volume)
        say(f"[OK] LIZ_DATA_DIR = {volume}")

        if args.no_healthz:
            say("[--] 健康检查端口：已禁用")
        else:
            check_port(args.port)
            env["HEALTHZ_PORT"] = str(args.port)
            say(f"[OK] HEALTHZ_PORT = {args.port}")
            watch = HealthWatch(args.port)

    # ---- 启动 -----------------------------------------------------------
    section("启动机器人")
    say(f"命令：{sys.executable} {RUN_PY.name}")
    say("（Ctrl+C 结束）")
    say()

    proc = subprocess.Popen(
        [sys.executable, str(RUN_PY)],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    # 子进程输出 → 直接透传（单独线程，主线程负责轮询健康检查）
    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            say(f"  | {line.rstrip()}")

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()

    if watch is not None:
        threading.Thread(target=watch.run, daemon=True).start()

    # ---- 等待 -----------------------------------------------------------
    try:
        code = proc.wait()
    except KeyboardInterrupt:
        say()
        say("收到 Ctrl+C，正在停止机器人 …")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        code = 0

    if watch is not None:
        watch.stop.set()

    # ---- 收尾 -----------------------------------------------------------
    section("结果")
    say(f"机器人退出码：{code}")

    if code != 0:
        say()
        say("启动失败。常见原因：")
        say("  * 凭据错误 / 已在别处运行 —— 同一个机器人不能两处同时在线")
        say("  * 依赖缺失或 Python 版本不符")
        say("  * 端口被占用（若用的是 --port 指定的端口）")
        return code

    if watch is not None and watch.reached:
        say("[OK] 健康检查端点全程正常")
    if watch is not None and watch.last == "ready":
        say("[OK] 机器人在线（曾连上 QQ）")

    say()
    say("接下来在 QQ 群里验证功能（这一步本地和云端都一样）：")
    say("  1. 群里 @机器人 发  /id 8        → 应返回《True Love Song》")
    say("  2. 发一个不存在的指令            → 应返回「未知指令」而非静默")
    say("  3. 试一条别名新增指令，然后 **再跑一次本脚本**")
    say("     → 应看到「卷上已有 alias.json，本次不会覆盖」")
    say("     这就是「别名在重启后不丢」的证明，也是云端最容易出事的一点")
    say()
    say(f"卷目录：{env.get('LIZ_DATA_DIR', '（未启用）')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
