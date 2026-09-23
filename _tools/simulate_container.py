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

提交前验证
----------
默认模拟的是 `HEAD`(平台会拉到的那份),所以**未提交的改动它看不到** ——
而"改完 .dockerignore / 新增数据文件,想先验一遍"恰恰是最需要它的时刻。

解法:造一个**悬空 commit**(不移动分支、不动工作区,跑完即弃)再喂给 `--ref`::

    git add -A
    TREE=$(git write-tree)
    TMP=$(git commit-tree "$TREE" -p HEAD -m "simulate tmp")
    git reset                    # 只重置索引,取消暂存,回到原状
    python _tools/simulate_container.py --ref "$TMP"

`git commit-tree` 只写对象、不移动任何分支;`git reset`(不带 `--hard`)
只重置索引 —— 工作区内容自始至终未被改动。那个临时 commit 无人引用,
之后会被 gc 自动回收。

    python _tools/simulate_container.py              # 模拟 HEAD(默认)
    python _tools/simulate_container.py --ref <sha>  # 模拟指定版本
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
    """挑一个能 ``import botpy`` 的解释器(查歌链路所需的第三方依赖)。"""
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
            proc = subprocess.run(cand + ["-c", "import botpy"],
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

def export_ref(dest: Path, ref: str) -> list[str]:
    """把 ref 的内容解到 dest,返回相对路径列表。"""
    out = subprocess.run(["git", "archive", "--format=tar", ref],
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


def main(ref: str = "HEAD") -> None:
    print(f"解释器:{' '.join(PY)}")
    print(f"仓库根:{REPO}")
    head = subprocess.run(["git", "rev-parse", ref], cwd=str(REPO),
                          capture_output=True, text=True, check=True).stdout.strip()
    print(f"版本  :{head}({ref}{'，已推送的那份内容' if ref == 'HEAD' else ''})")
    print()

    pats = load_dockerignore(REPO / ".dockerignore")

    with tempfile.TemporaryDirectory(prefix="lizimg_") as tmp:
        img = Path(tmp) / "image"
        img.mkdir()

        tracked = export_ref(img, ref)
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
            "liz_bot/replies.py",
            "liz_bot/pic_haddler.py",
            "liz_bot/text.py",
            "liz_bot/divingfish_songs/music_data.json",
            # 别名库与曲库同理：缺了它 /别名查歌、/查询别名、/cbm
            # 会静默返回"没有找到"，而容器本身照常启动 —— 典型的静默丢数据。
            "liz_bot/yuzuchan_aliases/aliases.json",
            # 回复文本：缺了它**启动就会失败**（run.py 会先 preload 并报错退出），
            # 属于 fail-fast，不会静默降级 —— 但也正因为如此，少了它容器起不来。
            "liz_bot/texts/replies.json",
            "liz_bot/config/config.example.yaml",
            "liz_bot/config/bot-config.example.yaml",
            "liz_bot/config/ai_config.example.yaml",
        ]
        missing = [r for r in required if r not in kept]
        check(not missing, f"{len(required)} 个必需文件全部在镜像内",
              ("缺失:" + ", ".join(missing)) if missing else "")

        # 下面这条**不依赖 HEAD**：直接拿 .dockerignore 的规则去匹配只读数据
        # 文件。上面那条是基于 git 内容的，只有当文件**已提交**时才看得见；
        # 万一有人加了 `*.json` 或 `liz_bot/*` 这类规则，数据会被静默排除出
        # 镜像（机器人照常启动，只是查歌/别名全部"没有找到"），这条能立刻抓住。
        for rel in ("liz_bot/divingfish_songs/music_data.json",
                    "liz_bot/yuzuchan_aliases/aliases.json",
                    "liz_bot/texts/replies.json"):
            check(not is_excluded(rel, pats), f".dockerignore 未排除 {rel}")

        # --------------------------------------------------- 不该有的必须没有
        print()
        print("=" * 72)
        print("D. 敏感/无用内容必须不进镜像")
        print("=" * 72)

        # 注意 liz_bot/maimaiDX_songs 是**故意保留**的哨兵：该旧曲库已于
        # 2026-09-23 归档到 _backup/，.dockerignore 里针对它的排除规则也已
        # 删除。若哪天它被重新放回仓库，就会漏进镜像并被下面这条检查抓住。
        # （liz_bot/divingfish_songs 则**必须**在镜像内，不在本列表里。）
        forbidden_dirs = [
            "_backup", "legacy_sdgb_stack", "legacy_qqbot_stack",
            "_tools", "bot_log", ".git", "__pycache__", "liz_bot/emoji_gif",
            "liz_bot/ai_chat", "liz_bot/maimaiDX_songs",
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

        # E0: 回复文本缺失时必须**明确报错退出**，而不是静默降级。
        #     这是 replies.json 的 fail-fast 契约(见 liz_bot/replies.py)——
        #     刻意不在代码里留一份兜底文案，否则会出现"改了文件却没生效"
        #     这种最难查的情况。把文件挪走跑一次入口，跑完立刻还原。
        replies_file = img / "liz_bot" / "texts" / "replies.json"
        stashed = replies_file.with_name("replies.json.bak")
        replies_file.rename(stashed)
        try:
            proc0 = subprocess.run(PY + ["run.py"], cwd=str(img), env=env,
                                   capture_output=True, text=True, timeout=120)
            check(proc0.returncode == 1,
                  "回复文本缺失时退出码为 1", f"实际 {proc0.returncode}")
            check("回复文本文件不存在" in proc0.stderr,
                  "缺失时打印清晰提示而非 traceback", proc0.stderr.strip()[:160])
            check("Traceback" not in proc0.stderr, "缺失时 stderr 无 Traceback")
        finally:
            stashed.rename(replies_file)

        proc = subprocess.run(PY + ["run.py"], cwd=str(img), env=env,
                              capture_output=True, text=True, timeout=120)
        check(proc.returncode == 1,
              "无配置时退出码为 1", f"实际 {proc.returncode}")
        check("缺少机器人配置" in proc.stderr,
              "打印的是清晰中文错误而非 traceback", proc.stderr.strip()[:160])
        check("Traceback" not in proc.stderr, "stderr 无 Traceback")

        # E2: 设 LIZ_DATA_DIR 后应识别为数据卷并把可写目录建在卷上
        vol = Path(tmp) / "vol"
        env2 = dict(env)
        env2["LIZ_DATA_DIR"] = str(vol)
        probe = """
import json, os, sys
sys.path.insert(0, os.getcwd())
from liz_bot import runtime_paths as rp
from liz_bot.song_paths import SONG_FILE_PATH
notes = rp.ensure_dirs()
print(json.dumps({
    "volume_backed": rp.is_volume_backed(),
    "ai_chat": rp.AI_CHAT_DIR,
    "log_dir": rp.LOG_DIR,
    "song_dir": SONG_FILE_PATH,
    "ai_chat_on_vol": os.path.isdir(rp.AI_CHAT_DIR),
    "log_on_vol": os.path.isdir(rp.LOG_DIR),
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
            check(str(d["ai_chat"]).startswith(str(vol)), "AI_CHAT_DIR 落在卷上", d["ai_chat"])
            check(str(d["log_dir"]).startswith(str(vol)), "LOG_DIR 落在卷上", d["log_dir"])
            check(d["song_dir"].endswith("divingfish_songs"), "曲库基线仍在镜像内")
            check(d["ai_chat_on_vol"] and d["log_on_vol"], "可写目录已在卷上创建")

        # E3: 查歌链路端到端(证明 music_data.json 完整 + 索引可用)
        # 注意 handle_command 是 **async** 的 —— 直接调用只会拿到协程对象,
        # 不 await 的话所有断言都会"通过"(协程的 repr 里当然不含"没有找到"),
        # 属于典型的假通过。必须 asyncio.run。
        #
        # 别名三例是 2026-09-23 新增的:别名库 aliases.json 是**新的运行时
        # 依赖**,而它缺失时的表现同样是静默的(返回"没有找到")。
        # 特意挑了两个非曲名别名:
        #   * "会员制餐厅"  —— 纯别名命中(曲名里没有这两个字),证明别名索引真的加载了
        #   * "TRUE LOVE SONG" —— 含空格,走 command_router 的整串优先路径
        probe2 = """
import asyncio, json, os, sys
sys.path.insert(0, os.getcwd())
from liz_bot.command_handler import handle_command

CASES = (
    ("id", "id", ["8"]),
    ("random", "random", []),
    ("nosuchcmd", "nosuchcmd", []),
    ("bm_alias", "bm", ["会员制餐厅"]),
    ("bm_multiword", "bm", ["TRUE LOVE SONG"]),
    ("cbm", "cbm", ["8"]),
)

async def main():
    out = {}
    for key, name, params in CASES:
        try:
            out[key] = str(await handle_command(name, params))[:160]
        except Exception as e:
            out[key] = f"<{type(e).__name__}: {e}>"
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

            # 别名链路 —— 数据缺失时这里会返回"没有找到",而容器照样起得来
            # 断言用 "id：8" 而不是曲名:别名 '会员制餐厅' 同时挂在 8 和 67 上,
            # 曲名会随索引顺序变,id 才是这条用例真正要钉的东西。
            check(not r["bm_alias"].startswith("<"), "别名查歌 /bm 无异常", r["bm_alias"])
            check("没有找到" not in r["bm_alias"] and "id：8" in r["bm_alias"],
                  "别名库完整(纯别名 '会员制餐厅' 命中 id 8)", r["bm_alias"])
            check("没有找到" not in r["bm_multiword"] and "id：8" in r["bm_multiword"],
                  "含空格的别名可用(router 整串优先)", r["bm_multiword"])
            check("会员制餐厅" in r["cbm"],
                  "/cbm 能回显别名列表", r["cbm"])

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
    import argparse

    ap = argparse.ArgumentParser(
        description="模拟容器构建：验证 .dockerignore 没误伤运行必需文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument(
        "--ref", default="HEAD",
        help="要模拟的版本（默认 HEAD）。可传任意 ref / commit sha —— "
             "想在**提交前**先验一遍，见模块文档「提交前验证」。",
    )
    main(ap.parse_args().ref)
