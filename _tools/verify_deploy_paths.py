"""验证部署相关改动：路径路由 / 空卷播种 / 播种不覆盖 / 健康检查 / 别名库 / 回归。

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
    """挑一个能 ``import botpy`` 的解释器。

    本项目运行依赖装在**系统 Python 3.10** 上，而 PATH 上的 ``python``
    可能指向 .venv（3.12/3.13，没装 botpy）。用错解释器会误报
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
                cand + ["-c", "import botpy"],
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
from liz_bot import replies, runtime_paths as rp
from liz_bot.song_paths import (
    ALIASES_JSON, ALIAS_FILE_PATH, MUSIC_DATA_JSON, SONG_FILE_PATH, song_file,
)
out = {
    "music_data": MUSIC_DATA_JSON,
    "song_dir": SONG_FILE_PATH,
    "alias_dir": ALIAS_FILE_PATH,
    "aliases_json": ALIASES_JSON,
    "text_dir": rp.TEXT_FILE_PATH,
    "replies_json": replies.REPLIES_JSON,
    "log_dir": rp.LOG_DIR,
    "ai_chat": rp.AI_CHAT_DIR,
    "data_root": rp.DATA_ROOT,
    "volume_backed": rp.is_volume_backed(),
    "song_file_music_data": song_file("music_data.json"),
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
expect_song_dir = str(REPO / "liz_bot" / "divingfish_songs")
expect_alias_dir = str(REPO / "liz_bot" / "yuzuchan_aliases")
expect_text_dir = str(REPO / "liz_bot" / "texts")
expect_log = str(REPO / "bot_log")
expect_chat = str(REPO / "liz_bot" / "ai_chat")

check(base["data_root"] is None, "DATA_ROOT 为 None（未启用数据卷）")
check(not base["volume_backed"], "is_volume_backed() 为 False")
check(base["song_dir"] == expect_song_dir, "SONG_FILE_PATH 落在仓库内", base["song_dir"])
check(
    base["music_data"] == str(Path(expect_song_dir) / "music_data.json"),
    "MUSIC_DATA_JSON 正确",
)
check(base["alias_dir"] == expect_alias_dir, "ALIAS_FILE_PATH 落在仓库内", base["alias_dir"])
check(
    base["aliases_json"] == str(Path(expect_alias_dir) / "aliases.json"),
    "ALIASES_JSON 正确",
    base["aliases_json"],
)
check(base["text_dir"] == expect_text_dir, "TEXT_FILE_PATH 落在仓库内", base["text_dir"])
check(
    base["replies_json"] == str(Path(expect_text_dir) / "replies.json"),
    "REPLIES_JSON 正确",
    base["replies_json"],
)
check(base["log_dir"] == expect_log, "LOG_DIR 为项目根 bot_log/", base["log_dir"])
check(base["ai_chat"] == expect_chat, "AI_CHAT_DIR 为 liz_bot/ai_chat/", base["ai_chat"])
check(
    base["song_file_music_data"] == base["music_data"],
    "song_file('music_data.json') 仍指向仓库内只读文件",
)

print()
print("=" * 72)
print("B. 挂载数据卷 —— 可写路径应重定向到卷，只读基线留在原处")
print("=" * 72)

with tempfile.TemporaryDirectory(prefix="lizvol_") as vol:
    volp = Path(vol).resolve()
    v = run_probe(PATHS_PROBE, {"LIZ_DATA_DIR": str(volp)})

    check(v["volume_backed"], "is_volume_backed() 为 True")
    check(v["log_dir"] == str(volp / "bot_log"), "LOG_DIR 重定向到卷", v["log_dir"])
    check(v["ai_chat"] == str(volp / "ai_chat"), "AI_CHAT_DIR 重定向到卷", v["ai_chat"])
    check(
        v["song_dir"] == expect_song_dir,
        "SONG_FILE_PATH 不受影响（只读基线仍在镜像内）",
        v["song_dir"],
    )
    check(
        v["alias_dir"] == expect_alias_dir,
        "ALIAS_FILE_PATH 不受影响（只读基线仍在镜像内）",
        v["alias_dir"],
    )
    check(
        v["text_dir"] == expect_text_dir,
        "TEXT_FILE_PATH 不受影响（只读基线仍在镜像内）",
        v["text_dir"],
    )
    check((volp / "bot_log").is_dir(), "bot_log/ 已在卷上创建")
    check((volp / "ai_chat").is_dir(), "ai_chat/ 已在卷上创建")

    print()
    print("=" * 72)
    print("C. 启动不得改动卷上已有数据 + 别名写入已停用")
    print("=" * 72)

    # C1: 卷上已有数据不能被启动流程碰掉
    marker = volp / "ai_chat" / "__verify_marker__.json"
    marker.write_text('{"keep": true}', encoding="utf-8")

    run_probe(PATHS_PROBE, {"LIZ_DATA_DIR": str(volp)})
    check(marker.is_file() and marker.read_text(encoding="utf-8") == '{"keep": true}',
          "二次启动未改动卷上已有数据")

    # C2: 别名功能已移除 —— add_song_alias 应是纯 no-op，不写任何文件
    w = run_probe(
        """
import json, os, sys
sys.path.insert(0, os.getcwd())
from liz_bot import runtime_paths as rp, song_alias
rp.ensure_dirs()
before = sorted(os.listdir(rp.AI_CHAT_DIR))
ok = song_alias.add_song_alias("__verify_song__", "卷上别名")
after = sorted(os.listdir(rp.AI_CHAT_DIR))
print(json.dumps({
    "ok": ok,
    "enabled": song_alias.ENABLED,
    "reply": song_alias.add_alias_reply("__verify_song__", "x"),
    "untouched": before == after,
}))
""",
        {"LIZ_DATA_DIR": str(volp)},
    )
    check(w["ok"] is False, "add_song_alias 恒返回 False（未写入）")
    check(w["enabled"] is False, "song_alias.ENABLED 为 False")
    check(bool(w["reply"]), "add_alias_reply 返回非空文本（空串会被 QQ 接口拒绝）")
    check(w["untouched"], "别名调用未产生任何文件写入")

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
print("H. 别名库 —— 数据在位、能关联上曲库、匹配语义与防劫持")
print("=" * 72)

# 别名库（yuzuchan_aliases/aliases.json）是 2026-09-23 新增的**运行时依赖**。
# 它出问题的方式同样是静默的：文件缺失/被 .dockerignore 误伤时，索引为空，
# /别名查歌 与 /查询别名 只会回一句"没有找到"，而机器人照常启动。
#
# 这里顺带把两条**语义契约**钉住（都是照搬柚子 API 的实测行为）：
#   1. 别名匹配是"大小写不敏感 + 精确"，不是子串 —— 故 '真' 不该命中。
#   2. BY_ANY 的顺序是 歌名 → ID → 别名 —— 故别名 '9'（属于 302）不得劫持
#      /song 9（Color My World）。这是别名兜底排在最后的原因。
ALIAS_PROBE = """
import json, os, sys
sys.path.insert(0, os.getcwd())
from liz_bot import song_query as sq
from liz_bot.song_paths import ALIASES_JSON

def ids(keyword, kind):
    # 注意：曲库里的 id 是**字符串**（水鱼返回如此），别直接和 int 比。
    res = sq.select_song(keyword, kind)
    return [int(x["song"]["id"]) for x in res] if res else []

print(json.dumps({
    "path": ALIASES_JSON,
    "exists": os.path.isfile(ALIASES_JSON),
    "size": os.path.getsize(ALIASES_JSON) if os.path.isfile(ALIASES_JSON) else 0,
    "stats": sq.library_stats(),
    "id_type": type(sq.select_song("8", sq.BY_ID)[0]["song"]["id"]).__name__,
    "by_alias": ids("会员制餐厅", sq.BY_ALIAS),
    "by_alias_upper": ids("TRUE LOVE SONG", sq.BY_ALIAS),
    "substring_probe": ids("真", sq.BY_ALIAS),
    "by_any_numeric": ids("9", sq.BY_ANY),
    "by_id_numeric": ids("9", sq.BY_ID),
    "reply8": sq.alias_reply("8"),
    "reply_missing": sq.alias_reply("99999"),
    "not_found": sq.NOT_FOUND,
}, ensure_ascii=False))
"""

al = run_probe(ALIAS_PROBE)
stats = al["stats"]

check(al["exists"], "aliases.json 存在于仓库内", al["path"])
check(al["size"] > 200_000, f"aliases.json 体积正常（{al['size']:,} 字节）")
check(al["id_type"] == "str",
      "曲库 id 是字符串（水鱼如此，比较前必须 _as_int 归一）", al["id_type"])
check(stats.get("aliases", 0) >= 9000,
      f"别名条数正常（{stats.get('aliases')} 条）")
check(stats.get("alias_keys", 0) >= 8000,
      f"去重后的别名键正常（{stats.get('alias_keys')} 个）")
check(8 in al["by_alias"],
      "别名索引可用（'会员制餐厅' → id 8）", str(al["by_alias"]))
check(al["by_alias_upper"] == [8],
      "别名匹配大小写不敏感（'TRUE LOVE SONG' → id 8）", str(al["by_alias_upper"]))
check(al["substring_probe"] == [],
      "别名是精确匹配而非子串（'真' 不命中 '真爱'）", str(al["substring_probe"]))
check(al["by_any_numeric"] == [9] and al["by_id_numeric"] == [9],
      "数字别名不劫持 ID 查询（'9' → Color My World）",
      f"BY_ANY={al['by_any_numeric']} BY_ID={al['by_id_numeric']}")
check("会员制餐厅" in al["reply8"],
      "/查询别名 保持旧文本格式且含真实数据", al["reply8"])
check(al["reply_missing"] == al["not_found"],
      "未知 ID 返回统一的未找到文案", al["reply_missing"])

print()
print("=" * 72)
print("I. 回复文本 —— 文案来自文件、可热更新、缺失即报错")
print("=" * 72)

# replies.json 是 2026-09-23 新增的**运行时依赖**：全部用户可见文案都在里面。
# 它的取向与别的数据文件相反 —— 刻意**不做**静默降级：文件缺失 / JSON 损坏 /
# 缺键 / 占位符写错一律抛 RepliesError，再由 run.py 变成一行启动错误。
# 理由：文案文件若静默回退到代码内置默认值，就会出现"改了文件却没生效"
# 这种最难排查的情况。
#
# 注意本段**只断言结构性不变量，不硬编码文案内容** —— 文案本来就是给人随时改的，
# 把具体字面量写进断言会让"改文案"变成"改测试"。默认文案是否与重构前逐字一致，
# 由 _tools/test_refactor_equivalence.py 负责把关（它拿原实现做对照）。
REPLIES_PROBE = """
import json, os, shutil, sys, tempfile, time
sys.path.insert(0, os.getcwd())
from liz_bot import replies

out = {"path": replies.REPLIES_JSON, "exists": os.path.isfile(replies.REPLIES_JSON)}
out["missing_keys"] = sorted(set(replies._SCHEMA) - set(replies.keys()))
out["extra_keys"] = sorted(set(replies.keys()) - set(replies._SCHEMA))

# ---- 正常取值：只验"能取到、非空、占位符确实被替换" ----
out["not_found"] = replies.text("song.not_found")
out["missing"] = replies.text("song.missing")
out["none_reply"] = replies.get("bot.none_reply")
out["greeting"] = replies.text("daily.greeting")
out["help"] = replies.text("daily.help")
out["add_failed"] = replies.text("alias.add_failed")
out["add_bad_params"] = replies.text("alias.add_bad_params")
out["data_error"] = replies.text("song.data_error")
out["bad_params"] = replies.text("song.bad_params")
out["unknown"] = replies.text("router.unknown_command", cmd_name="ZZZTOKEN")
out["not_command"] = replies.text("bot.not_command", content="ZZZTOKEN")
out["error"] = replies.text("bot.error", error="ZZZTOKEN")
out["alias_header"] = replies.text("song.alias_header", aliases=["a", "b"])
out["format_rendered"] = replies.text(
    "song.format", title="ZZZTOKEN", artist="ZZZTOKEN", id="ZZZTOKEN",
    master_ds="ZZZTOKEN", master_charter="ZZZTOKEN",
    rem_ds="ZZZTOKEN", rem_charter="ZZZTOKEN")

# ---- 报错路径：下面每一条都**必须**抛 RepliesError（不能静默降级）----
def err(fn):
    try:
        fn()
        return None
    except replies.RepliesError as exc:
        return str(exc).splitlines()[0]
    except Exception as exc:            # 抛错类型不对同样算失败
        return "<%s>" % type(exc).__name__

good = json.load(open(replies.REPLIES_JSON, encoding="utf-8"))
tmp = tempfile.mkdtemp(prefix="lizreplies_")
probe_file = os.path.join(tmp, "replies.json")
replies.REPLIES_JSON = probe_file

def write(payload):
    with open(probe_file, "w", encoding="utf-8") as fh:
        if isinstance(payload, str):
            fh.write(payload)
        else:
            json.dump(payload, fh, ensure_ascii=False)

replies.reload()
out["err_missing_file"] = err(lambda: replies.text("song.not_found"))

write('{ "song": { "not_found": "x", } }')          # 多余逗号
out["err_bad_json"] = err(lambda: replies.text("song.not_found"))

no_key = json.loads(json.dumps(good))
del no_key["song"]["not_found"]
write(no_key)
out["err_missing_key"] = err(lambda: replies.text("song.not_found"))

bad_tpl = json.loads(json.dumps(good))
bad_tpl["song"]["format"] = "标题：{titel}"           # 占位符拼错
write(bad_tpl)
out["err_bad_placeholder"] = err(lambda: replies.text("song.not_found"))

# ---- 热更新：改文件后取值应立刻变化 ----
write(good)
replies.reload()
out["hot_before"] = replies.text("song.not_found")
mod = json.loads(json.dumps(good))
mod["song"]["not_found"] = "ZZZTOKEN"
time.sleep(0.05)
write(mod)
out["hot_after"] = replies.text("song.not_found")

shutil.rmtree(tmp, ignore_errors=True)
print(json.dumps(out, ensure_ascii=False))
"""

rt = run_probe(REPLIES_PROBE)

check(rt["exists"], "replies.json 存在于仓库内", rt["path"])
check(not rt["missing_keys"], "键齐全（_SCHEMA 声明的键都在）", str(rt["missing_keys"]))
if rt["extra_keys"]:
    print(f"        （提示：有 {len(rt['extra_keys'])} 个未使用的键：{rt['extra_keys']}）")

for key in ("not_found", "missing", "greeting", "help", "add_failed",
            "add_bad_params", "data_error", "bad_params"):
    check(bool(rt[key]) and isinstance(rt[key], str),
          f"{key} 可取到且为非空字符串", repr(rt[key]))
check(isinstance(rt["none_reply"], list) and len(rt["none_reply"]) >= 1,
      "bot.none_reply 为非空数组", str(rt["none_reply"]))

# 占位符是否真的被替换（只查"替换进去了"，不查外围文案）
check("ZZZTOKEN" in rt["unknown"], "未知指令模板已替换 cmd_name", rt["unknown"])
check("ZZZTOKEN" in rt["not_command"], "非指令提示已替换 content", rt["not_command"])
check("ZZZTOKEN" in rt["error"], "异常提示已替换 error", rt["error"])
check("'a', 'b'" in rt["alias_header"], "别名头部已替换 aliases", rt["alias_header"])
check(rt["format_rendered"].count("ZZZTOKEN") == 7,
      "查歌排版模板的 7 个占位符全部被替换",
      f"实际出现 {rt['format_rendered'].count('ZZZTOKEN')} 次")

check(bool(rt["err_missing_file"]),
      "文件缺失时抛 RepliesError（不静默降级）", str(rt["err_missing_file"]))
check(bool(rt["err_bad_json"]), "JSON 损坏时抛 RepliesError", str(rt["err_bad_json"]))
check(bool(rt["err_missing_key"]), "缺键时抛 RepliesError", str(rt["err_missing_key"]))
check(bool(rt["err_bad_placeholder"]),
      "占位符写错时抛 RepliesError", str(rt["err_bad_placeholder"]))
check(rt["hot_after"] == "ZZZTOKEN" and rt["hot_before"] != rt["hot_after"],
      "改文件后取值立刻变化（热更新生效）",
      f"{rt['hot_before']!r} -> {rt['hot_after']!r}")

print()
print("=" * 72)
print("E. 语法编译")
print("=" * 72)

# ⚠️ 别写死清单。原先这里只列了 12 个文件，`judge_detail` / `judge_image` /
# `score_estimate` / `estimate_image` / `pending` / `media_upload` / `text_layout`
# 全都不在 —— 而 `command_router` 在 import 期就导入其中好几个，
# 它们语法错误会让 bot 直接起不来，这个「部署面语法检查」却看不见。
# 改成 glob：以后新增模块自动纳入，不需要记得回来补一行。
targets = ["run.py", *sorted(
    p.relative_to(REPO).as_posix() for p in (REPO / "liz_bot").glob("*.py")
)]
proc = subprocess.run(
    PY + ["-m", "py_compile", *targets],
    cwd=str(REPO), capture_output=True, text=True,
)
check(proc.returncode == 0, f"py_compile 通过（{len(targets)} 个文件）", proc.stderr.strip())
check(len(targets) >= 20,
      f"编译清单覆盖 liz_bot 全部模块（实际 {len(targets)} 个文件）",
      f"liz_bot 下有 {len(list((REPO / 'liz_bot').glob('*.py')))} 个 .py")

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
