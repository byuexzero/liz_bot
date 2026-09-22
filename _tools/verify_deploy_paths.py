"""验证部署相关改动：路径路由 / 空卷播种 / 播种不覆盖 / 健康检查 / 回归。

本脚本不依赖 botpy，只用标准库，因此可在任意 Python 3.10+ 上运行。

    python _tools/verify_deploy_paths.py

注意：runtime_paths 的 DATA_ROOT 在 **import 时**求值，所以涉及 LIZ_DATA_DIR
的用例必须在子进程里跑（脚本内用 subprocess 自行处理）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _detect_python() -> list[str]:
    """挑一个能 ``import ijson, botpy`` 的解释器。

    本项目运行依赖装在**系统 Python 3.10** 上，而 PATH 上的 ``python``
    可能指向 .venv（3.12/3.13，没装 ijson）。用错解释器会误报
    ``ModuleNotFoundError``，看起来像代码坏了 —— 所以这里显式挑一个。
    """
    cands: list[list[str]] = [[sys.executable]]
    if shutil.which("py"):
        cands += [["py", v] for v in ("-3.10", "-3.12", "-3.13")]
    for root in (
        Path.home() / "AppData" / "Local" / "Programs" / "Python",
        Path("C:/Python310"),
        Path("C:/Python312"),
    ):
        if root.is_dir():
            cands += [[str(exe)] for exe in sorted(root.glob("Python3*/python.exe"))]

    for cand in cands:
        try:
            proc = subprocess.run(
                cand + ["-c", "import ijson, botpy"],
                capture_output=True, timeout=90,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0:
            return cand
    return [sys.executable]


PY = _detect_python()

_results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    _results.append((ok, label))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {label}" + (f"\n        {detail}" if detail else ""))


def run_probe(code: str, env_extra: dict[str, str] | None = None) -> dict:
    """在子进程中执行一段探针代码，返回其打印的 JSON。"""
    env = dict(os.environ)
    env.pop("LIZ_DATA_DIR", None)
    env.pop("HEALTHZ_PORT", None)
    env.pop("PORT", None)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        PY + ["-c", code],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"探针失败：\n{proc.stdout}\n{proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


PATHS_PROBE = """
import json, os, sys
sys.path.insert(0, os.getcwd())
from liz_bot import runtime_paths as rp
from liz_bot.song_paths import ALIAS_JSON, SONGS_JSON, SONG_FILE_PATH, song_file
out = {
    "alias": ALIAS_JSON,
    "songs": SONGS_JSON,
    "song_dir": SONG_FILE_PATH,
    "log_dir": rp.LOG_DIR,
    "ai_chat": rp.AI_CHAT_DIR,
    "data_root": rp.DATA_ROOT,
    "volume_backed": rp.is_volume_backed(),
    "song_file_alias": song_file("alias.json"),
    "song_file_songs": song_file("songs.json"),
    "baseline": rp.BASELINE_ALIAS_JSON,
    "notes": rp.ensure_dirs(),
}
print(json.dumps(out))
"""

print(f"解释器：{' '.join(PY)}")
print(f"仓库根：{REPO}")
print()
print("=" * 72)
print("A. 回归 —— 未设置 LIZ_DATA_DIR 时，路径应与改动前完全一致")
print("=" * 72)

base = run_probe(PATHS_PROBE)
expect_song_dir = str(REPO / "liz_bot" / "maimaiDX_songs")
expect_alias = str(REPO / "liz_bot" / "maimaiDX_songs" / "alias.json")
expect_log = str(REPO / "bot_log")
expect_chat = str(REPO / "liz_bot" / "ai_chat")

check(base["data_root"] is None, "DATA_ROOT 为 None（未启用数据卷）")
check(not base["volume_backed"], "is_volume_backed() 为 False")
check(base["alias"] == expect_alias, "ALIAS_JSON 落在仓库内", base["alias"])
check(base["song_dir"] == expect_song_dir, "SONG_FILE_PATH 落在仓库内", base["song_dir"])
check(base["songs"] == str(Path(expect_song_dir) / "songs.json"), "SONGS_JSON 正确")
check(base["log_dir"] == expect_log, "LOG_DIR 为项目根 bot_log/", base["log_dir"])
check(base["ai_chat"] == expect_chat, "AI_CHAT_DIR 为 liz_bot/ai_chat/", base["ai_chat"])
check(
    base["song_file_alias"] == base["alias"],
    "song_file('alias.json') 路由到可写 ALIAS_JSON",
)
check(
    base["song_file_songs"] == base["songs"],
    "song_file('songs.json') 仍指向仓库内只读文件",
)

print()
print("=" * 72)
print("B. 挂载数据卷 —— 可写路径应重定向到卷，只读基线留在原处")
print("=" * 72)

with tempfile.TemporaryDirectory(prefix="lizvol_") as vol:
    volp = Path(vol).resolve()
    v = run_probe(PATHS_PROBE, {"LIZ_DATA_DIR": str(volp)})

    check(v["volume_backed"], "is_volume_backed() 为 True")
    check(v["alias"] == str(volp / "alias.json"), "ALIAS_JSON 重定向到卷", v["alias"])
    check(v["log_dir"] == str(volp / "bot_log"), "LOG_DIR 重定向到卷", v["log_dir"])
    check(v["ai_chat"] == str(volp / "ai_chat"), "AI_CHAT_DIR 重定向到卷", v["ai_chat"])
    check(
        v["song_dir"] == expect_song_dir,
        "SONG_FILE_PATH 不受影响（只读基线仍在镜像内）",
        v["song_dir"],
    )
    check(
        v["song_file_alias"] == v["alias"],
        "song_file('alias.json') 跟随卷路径（不会误拿到只读基线）",
    )

    seeded = volp / "alias.json"
    check(seeded.is_file(), "空卷首次启动已播种 alias.json")
    check(
        seeded.read_bytes() == Path(base["baseline"]).read_bytes(),
        "播种内容与镜像内基线逐字节一致",
    )
    check((volp / "bot_log").is_dir(), "bot_log/ 已在卷上创建")
    check((volp / "ai_chat").is_dir(), "ai_chat/ 已在卷上创建")

    print()
    print("=" * 72)
    print("C. 播种不得覆盖卷上已有数据（用户增量必须安全）")
    print("=" * 72)

    marker = [{"name": "__verify_marker__", "alias": ["verify"]}]
    seeded.write_text(json.dumps(marker, ensure_ascii=False), encoding="utf-8")

    run_probe(PATHS_PROBE, {"LIZ_DATA_DIR": str(volp)})

    after = json.loads(seeded.read_text(encoding="utf-8"))
    check(after == marker, "二次启动未覆盖卷上已修改的 alias.json", str(after))

    # 再验一次：真实写入路径确实落在卷上
    w = run_probe(
        """
import json, os, sys
sys.path.insert(0, os.getcwd())
from liz_bot import runtime_paths as rp
from liz_bot.song_alias import add_song_alias
rp.ensure_dirs()
ok = add_song_alias("__verify_song__", "卷上别名", rp.ALIAS_JSON)
data = json.load(open(rp.ALIAS_JSON, encoding="utf-8"))
print(json.dumps({"ok": ok, "has": any(i.get("name") == "__verify_song__" for i in data)}))
""",
        {"LIZ_DATA_DIR": str(volp)},
    )
    check(w["ok"] and w["has"], "add_song_alias 实际写入卷上的 alias.json")

print()
print("=" * 72)
print("D. 健康检查端口")
print("=" * 72)

h0 = run_probe(
    """
import json, os, sys
sys.path.insert(0, os.getcwd())
from liz_bot import healthz
print(json.dumps({"port": healthz.start()}))
"""
)
check(h0["port"] == 0, "未设置端口时 start() 返回 0（no-op，本地不受影响）")

h1 = run_probe(
    """
import json, os, sys, time, urllib.request
sys.path.insert(0, os.getcwd())
from liz_bot import healthz
port = healthz.start()
time.sleep(0.3)
r = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5)
body = json.loads(r.read().decode())
healthz.set_status("ready")
r2 = urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5)
body2 = json.loads(r2.read().decode())
print(json.dumps({"port": port, "code": r.status, "body": body, "body2": body2}))
""",
    {"HEALTHZ_PORT": "18734"},
)
check(h1["port"] == 18734, "HEALTHZ_PORT 生效", str(h1["port"]))
check(h1["code"] == 200, "GET / 返回 200")
check(h1["body"]["status"] == "ok", "响应体含 status=ok", str(h1["body"]))
check(h1["body"]["bot"] == "starting", "初始状态为 starting")
check(h1["body2"]["bot"] == "ready", "set_status 后反映真实就绪状态", str(h1["body2"]))

h2 = run_probe(
    """
import json, os, sys
sys.path.insert(0, os.getcwd())
from liz_bot import healthz
print(json.dumps({"port": healthz.start()}))
""",
    {"PORT": "18735"},
)
check(h2["port"] == 18735, "平台注入的 PORT 也能被识别")

print()
print("=" * 72)
print("F. 入口脚本 run.py —— 路径打印 / 配置优先级 / 健康检查")
print("=" * 72)

# 把 run_bot 打桩，避免真的连上腾讯服务器（本机 liz_bot/config/config.yaml
# 里是真实凭据，直接跑会把线上机器人拉起来）。
ENTRY_PROBE = """
import json, os, sys
sys.path.insert(0, os.getcwd())
from liz_bot import qqgroupbot
calls = []
qqgroupbot.run_bot = lambda cfg: calls.append(repr(cfg))
import run
run.main()
print(json.dumps({"calls": calls}))
"""

e1 = run_probe(ENTRY_PROBE, {"HEALTHZ_PORT": "18740"})
check(
    len(e1["calls"]) == 1,
    "run.py 成功走到 run_bot 且只调用一次",
    str(e1["calls"]),
)
check(
    "secret=***" in (e1["calls"][0] if e1["calls"] else ""),
    "BotConfig repr 未泄漏 secret",
)

e2 = run_probe(
    ENTRY_PROBE,
    {
        "QQ_BOT_APPID": "ENV_APPID_SHOULD_WIN",
        "QQ_BOT_SECRET": "ENV_SECRET_SHOULD_WIN",
        "HEALTHZ_PORT": "18741",
    },
)
check(
    bool(e2["calls"]) and "ENV_APPID_SHOULD_WIN" in e2["calls"][0],
    "环境变量优先于本地 YAML",
    str(e2["calls"]),
)

print()
print("=" * 72)
print("G. botpy 集成 —— run_bot 传参与 on_ready 状态联动")
print("=" * 72)

# 这一节补的是一个真实缺口：前面 F 节把 run_bot 整个打桩了，healthz 也只
# 单独测过。于是"run_bot 有没有把注入的凭据真的交给 botpy"和
# "on_ready 有没有把健康检查状态置为 ready"这两件事从未被验证过 ——
# 而它们正是本次重构与健康检查接入的关键接线点。
# 手法：把 botpy.Client.run 换成记录器，就不必真的连腾讯服务器。
BOT_INTEGRATION_PROBE = """
import asyncio, json, os, sys, types
sys.path.insert(0, os.getcwd())
import botpy
import botpy.robot
from liz_bot import healthz, qqgroupbot
from liz_bot.config import BotConfig

out = {}

calls = []
def fake_run(self, *args, **kwargs):
    calls.append({"args": [str(a) for a in args],
                  "kwargs": {k: str(v) for k, v in kwargs.items()}})
botpy.Client.run = fake_run
qqgroupbot.run_bot(BotConfig(appid="APPID_X", secret="SECRET_Y"))
out["run_calls"] = calls

client = qqgroupbot.MyClient(intents=botpy.Intents(public_messages=True))
# Client.robot 是只读 property，真实数据来自 self._connection.state.robot
# （见 botpy/client.py: `return self._connection.state.robot`）。
# 所以这里不能直接赋值 .robot，只能注入 _connection；且刻意用 botpy 真实的
# Robot 类而不是 SimpleNamespace —— 这样连"on_ready 读取的字段名是否与
# botpy 一致"也一并验证了。
client._connection = types.SimpleNamespace(
    state=types.SimpleNamespace(
        robot=botpy.robot.Robot({"id": "12345", "username": "stub-bot"})
    )
)
out["robot_name_via_property"] = client.robot.name
out["before"] = healthz._state["bot"]
asyncio.run(client.on_ready())
out["after"] = healthz._state["bot"]

print(json.dumps(out))
"""

gi = run_probe(BOT_INTEGRATION_PROBE, {"HEALTHZ_PORT": "18742"})
calls = gi.get("run_calls") or []
check(len(calls) == 1, "run_bot 恰好调用 botpy.Client.run 一次", str(calls))
if calls:
    kw = calls[0].get("kwargs", {})
    check(kw.get("appid") == "APPID_X", "appid 被原样传给 botpy", str(kw))
    check(kw.get("secret") == "SECRET_Y", "secret 被原样传给 botpy", str(kw))
check(gi.get("before") == "starting", "on_ready 之前状态为 starting", str(gi.get("before")))
check(gi.get("after") == "ready", "on_ready 之后状态变为 ready", str(gi.get("after")))
check(
    gi.get("robot_name_via_property") == "stub-bot",
    "client.robot 经 _connection 读取到真实 botpy Robot",
    str(gi.get("robot_name_via_property")),
)

print()
print("=" * 72)
print("E. 语法编译")
print("=" * 72)

targets = [
    "run.py",
    "liz_bot/runtime_paths.py",
    "liz_bot/healthz.py",
    "liz_bot/song_paths.py",
    "liz_bot/qqgroupbot.py",
    "liz_bot/qqgroup-ai-bot.py",
    "liz_bot/song_alias.py",
    "liz_bot/song_query.py",
    "liz_bot/command_handler.py",
]
proc = subprocess.run(
    PY + ["-m", "py_compile", *targets],
    cwd=str(REPO), capture_output=True, text=True,
)
check(proc.returncode == 0, f"py_compile 通过（{len(targets)} 个文件）", proc.stderr.strip())

print()
print("=" * 72)
passed = sum(1 for ok, _ in _results if ok)
total = len(_results)
print(f"结果：{passed}/{total} 通过")
failed = [label for ok, label in _results if not ok]
if failed:
    print("失败项：")
    for label in failed:
        print(f"  - {label}")
    sys.exit(1)
print("全部通过。")
