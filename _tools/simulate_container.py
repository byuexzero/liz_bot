"""容器构建模拟 —— 不需要 Docker,也能验证 .dockerignore 没误伤运行必需文件。

为什么需要
----------
`.dockerignore` 写错的失败方式是**静默的**:被排除的文件不会进镜像,
容器要到真正用到它的时候才炸。本机没有 Docker,`docker build` 验证不了,
但可以精确复现"镜像里到底有哪些文件"这一步:

    1. `git archive HEAD` 取出**已推送的那份内容**(= 容器平台拉到的内容)
    2. 按 `.dockerignore` 规则删掉被排除的路径
    3. 断言该有的都在、不该有的都不在
    4. 在这个"模拟镜像"里真的跑一遍入口与查歌链路

注意第 1 步用的是 git 内容而非工作区,所以被 `.gitignore` 排除的文件
(如 `liz_bot/emoji/`、`liz_bot/config/config.yaml`)天然不在其中 ——
这与云端构建的行为一致。

.dockerignore 匹配语义
----------------------
Docker 用的是 Go 的 filepath.Match,与 shell 通配略有差异。本脚本实现了一个
**覆盖本项目实际写法**的近似匹配(精确路径 / 目录前缀 / 目录名 / 通配 / `!` 反排除,
后匹配者胜)。模式变复杂时应重新审视这里。

    python _tools/simulate_container.py
"""

from __future__ import annotations

import fnmatch
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _detect_python() -> list[str]:
    """挑一个能 ``import ijson, botpy`` 的解释器(查歌链路需要 ijson)。"""
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
            proc = subprocess.run(cand + ["-c", "import ijson, botpy"],
                                  capture_output=True, timeout=90)
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0:
            return cand
    return [sys.executable]


PY = _detect_python()
_results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    _results.append((ok, label))
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"\n        {detail}" if detail else ""))


# ---------------------------------------------------------------- dockerignore

def load_dockerignore(path: Path) -> list[tuple[bool, str]]:
    pats: list[tuple[bool, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        if negate:
            line = line[1:].strip()
        line = line.lstrip("/").rstrip("/")
        if line:
            pats.append((negate, line))
    return pats


def _matches(rel: str, pat: str) -> bool:
    if fnmatch.fnmatch(rel, pat):
        return True
    # 目录前缀: `_tools` 排除整个目录
    if rel == pat or rel.startswith(pat + "/"):
        return True
    # 目录名出现在任意层级: `__pycache__`
    if "/" not in pat and any(seg == pat for seg in rel.split("/")):
        return True
    return False


def is_excluded(rel: str, pats: list[tuple[bool, str]]) -> bool:
    excluded = False
    for negate, pat in pats:
        if _matches(rel, pat):
            excluded = not negate          # 后匹配者胜
    return excluded


# ------------------------------------------------------------------- 取仓库内容

def export_head(dest: Path) -> list[str]:
    """把 HEAD 的内容解到 dest,返回相对路径列表。"""
    out = subprocess.run(["git", "archive", "--format=tar", "HEAD"],
                         cwd=str(REPO), capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(out)) as tf:
        try:
            tf.extractall(dest, filter="data")   # Python 3.12+
        except TypeError:
            tf.extractall(dest)
    return sorted(
        p.relative_to(dest).as_posix()
        for p in dest.rglob("*") if p.is_file()
    )


def main() -> None:
    print(f"解释器:{' '.join(PY)}")
    print(f"仓库根:{REPO}")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO),
                          capture_output=True, text=True, check=True).stdout.strip()
    print(f"HEAD  :{head}(已推送的那份内容)")
    print()

    pats = load_dockerignore(REPO / ".dockerignore")

    with tempfile.TemporaryDirectory(prefix="lizimg_") as tmp:
        img = Path(tmp) / "image"
        img.mkdir()

        tracked = export_head(img)
        print("=" * 72)
        print(f"A. 取出仓库内容:{len(tracked)} 个文件")
        print("=" * 72)
        check(len(tracked) > 50, f"git archive 解出 {len(tracked)} 个文件")

        # ------------------------------------------------ 应用 .dockerignore
        kept, dropped = [], []
        for rel in tracked:
            if is_excluded(rel, pats):
                dropped.append(rel)
            else:
                kept.append(rel)
        for rel in dropped:
            (img / rel).unlink()

        print()
        print("=" * 72)
        print(f"B. 应用 .dockerignore:保留 {len(kept)},排除 {len(dropped)}")
        print("=" * 72)
        for rel in dropped:
            print(f"        - {rel}")

        # ------------------------------------------------------ 该有的必须在
        print()
        print("=" * 72)
        print("C. 运行必需文件必须保留")
        print("=" * 72)

        required = [
            "run.py",
            "requirements.txt",
            "Dockerfile",
            "liz_bot/runtime_paths.py",
            "liz_bot/healthz.py",
            "liz_bot/config.py",
            "liz_bot/qqgroupbot.py",
            "liz_bot/command_router.py",
            "liz_bot/command_handler.py",
            "liz_bot/daily_funcs.py",
            "liz_bot/song_query.py",
            "liz_bot/song_alias.py",
            "liz_bot/song_paths.py",
            "liz_bot/pic_haddler.py",
            "liz_bot/text.py",
            "liz_bot/maimaiDX_songs/songs.json",
            "liz_bot/maimaiDX_songs/alias.json",
            "liz_bot/config/config.example.yaml",
            "liz_bot/config/bot-config.example.yaml",
            "liz_bot/config/ai_config.example.yaml",
        ]
        missing = [r for r in required if r not in kept]
        check(not missing, f"{len(required)} 个必需文件全部在镜像内",
              ("缺失:" + ", ".join(missing)) if missing else "")

        # --------------------------------------------------- 不该有的必须没有
        print()
        print("=" * 72)
        print("D. 敏感/无用内容必须不进镜像")
        print("=" * 72)

        forbidden_dirs = [
            "_backup", "legacy_sdgb_stack", "legacy_qqbot_stack",
            "_tools", "bot_log", ".git", "__pycache__", "liz_bot/emoji_gif",
            "liz_bot/ai_chat", "liz_bot/maimaiDX_songs/compress",
            "liz_bot/maimaiDX_songs/.github",
        ]
        leaked = [
            k for k in kept
            if any(k == d or k.startswith(d + "/") for d in forbidden_dirs)
        ]
        check(not leaked, "排除目录无一泄漏进镜像", ", ".join(leaked[:5]))

        # 真实凭据文件 —— 这是最重要的一条
        cred_leak = [
            k for k in kept
            if k in (".env", "liz_bot/config/config.yaml",
                     "liz_bot/config/bot-config.yaml",
                     "liz_bot/config/ai_config.yaml")
        ]
        check(not cred_leak, "无任何真实凭据文件进镜像", ", ".join(cred_leak))
        check(
            not [k for k in kept if k.endswith(".pyc")],
            "无 .pyc 残留",
        )

        # ---------------------------------------------------------- 真跑一遍
        print()
        print("=" * 72)
        print("E. 在模拟镜像里真跑 —— 入口 / 卷 / 查歌")
        print("=" * 72)

        # E1: 无配置时应打印清晰错误并以 1 退出(而不是 traceback)
        env = {k: v for k, v in os.environ.items()
               if k not in ("QQ_BOT_APPID", "QQ_BOT_SECRET",
                            "LIZ_DATA_DIR", "HEALTHZ_PORT", "PORT")}
        proc = subprocess.run(PY + ["run.py"], cwd=str(img), env=env,
                              capture_output=True, text=True, timeout=120)
        check(proc.returncode == 1,
              "无配置时退出码为 1", f"实际 {proc.returncode}")
        check("缺少机器人配置" in proc.stderr,
              "打印的是清晰中文错误而非 traceback", proc.stderr.strip()[:160])
        check("Traceback" not in proc.stderr, "stderr 无 Traceback")

        # E2: 设 LIZ_DATA_DIR 后应识别为数据卷并播种
        vol = Path(tmp) / "vol"
        env2 = dict(env)
        env2["LIZ_DATA_DIR"] = str(vol)
        probe = """
import json, os, sys
sys.path.insert(0, os.getcwd())
from liz_bot import runtime_paths as rp
from liz_bot.song_paths import ALIAS_JSON, SONG_FILE_PATH
notes = rp.ensure_dirs()
print(json.dumps({
    "volume_backed": rp.is_volume_backed(),
    "alias": ALIAS_JSON,
    "song_dir": SONG_FILE_PATH,
    "seeded": os.path.exists(ALIAS_JSON),
    "seed_ok": os.path.exists(ALIAS_JSON) and
               os.path.getsize(ALIAS_JSON) == os.path.getsize(rp.BASELINE_ALIAS_JSON),
    "notes": notes,
}))
"""
        proc = subprocess.run(PY + ["-c", probe], cwd=str(img), env=env2,
                              capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            check(False, "卷探测脚本执行成功", proc.stderr.strip()[-300:])
        else:
            d = json.loads(proc.stdout.strip().splitlines()[-1])
            check(d["volume_backed"], "识别为数据卷")
            check(str(d["alias"]).startswith(str(vol)), "ALIAS_JSON 落在卷上", d["alias"])
            check(d["song_dir"].endswith("maimaiDX_songs"), "曲库基线仍在镜像内")
            check(d["seeded"] and d["seed_ok"], "基线别名表已播种到卷且大小一致")

        # E3: 查歌链路端到端(证明 songs.json 完整 + ijson 可用)
        # 注意 handle_command 是 **async** 的 —— 直接调用只会拿到协程对象,
        # 不 await 的话所有断言都会"通过"(协程的 repr 里当然不含"没有找到"),
        # 属于典型的假通过。必须 asyncio.run。
        probe2 = """
import asyncio, json, os, sys
sys.path.insert(0, os.getcwd())
from liz_bot.command_handler import handle_command

async def main():
    out = {}
    for name, params in (("id", ["8"]), ("random", []), ("nosuchcmd", [])):
        try:
            out[name] = str(await handle_command(name, params))[:60]
        except Exception as e:
            out[name] = f"<{type(e).__name__}: {e}>"
    return out

print(json.dumps(asyncio.run(main()), ensure_ascii=False))
"""
        proc = subprocess.run(PY + ["-c", probe2], cwd=str(img), env=env2,
                              capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            check(False, "指令链路可执行", proc.stderr.strip()[-300:])
        else:
            r = json.loads(proc.stdout.strip().splitlines()[-1])
            check(not r["id"].startswith("<"), "查歌 /id 8 无异常", r["id"])
            check("没有找到" not in r["id"], "查歌命中真实曲目(曲库完整)", r["id"])
            check(not r["random"].startswith("<"), "随机数指令正常", r["random"])
            check(r["nosuchcmd"] != "", "未知指令有回复而非静默", r["nosuchcmd"])

    print()
    print("=" * 72)
    passed = sum(1 for ok, _ in _results if ok)
    total = len(_results)
    print(f"结果:{passed}/{total} 通过")
    failed = [label for ok, label in _results if not ok]
    if failed:
        print("失败项:")
        for label in failed:
            print(f"  - {label}")
        sys.exit(1)
    print("模拟镜像可正常构建并运行。")


if __name__ == "__main__":
    main()
