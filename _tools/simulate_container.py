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
    5. 再核对 `deploy/docker-compose.yml` 与 Dockerfile / 卷 / 端口的一致性

注意第 1 步用的是 git 内容而非工作区,所以被 `.gitignore` 排除的文件
(如 `liz_bot/emoji/`、`liz_bot/config/config.yaml`)天然不在其中 ——
这与云端构建的行为一致。

第 5 步是唯一**不依赖 git** 的一节(直接读工作区):compose 文件是给人手动
`docker compose up` 用的,它此刻能不能跑与它有没有被提交无关,而它出错的
方式是静默的 —— 比如 `LIZ_DATA_DIR` 与卷挂载点写成两个路径,机器人照常
启动、照常回消息,只是日志与会话历史落进了容器层,重启就没。

.dockerignore 匹配语义
----------------------
本脚本移植了 ``moby/patternmatcher`` 的 ``Pattern.compile()`` 与
``MatchesOrParentMatches()``,而不是用 fnmatch / .gitignore 的近似 ——
**这三者的行为不一样,而差异恰好落在最容易出事的地方**。

要点(与 shell 通配、与 .gitignore 都不同):

- ``*`` 和 ``?`` **不跨 "/"**。``*.md`` 只匹配根目录的 .md;
- 不带 "/" 的名字是**精确匹配**,``__pycache__`` 只排除根目录那一个;
- 匹配任意层级必须显式写 ``**/``;``**/`` 也能匹配 0 层,
  所以 ``**/*.md`` 同时覆盖根目录与所有子目录;
- 模式命中某个目录 => 整棵子树被排除;
- 后匹配者胜,``!`` 反排除只在已匹配后生效。

历史教训(2026-09-25):旧实现按 .gitignore 语义做近似,比真实 Docker
**宽松**,于是把"其实会进镜像"的文件报成已排除 —— 方向正是最危险的一侧。
详见 ``is_excluded()`` 的 docstring。

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

import io
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from functools import lru_cache
from pathlib import Path

try:
    import yaml as _yaml
except ImportError:  # pragma: no cover - 只在依赖缺失时走到
    _yaml = None

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


# moby/patternmatcher 会把模式编译成这四种匹配之一。
# `.*+()|{}$` 在 Go 的 filepath.Match 里没有特殊含义,但在正则里有,
# 所以移植时要转义(与上游 shouldEscape 一致)。
_ESCAPE_IN_REGEX = frozenset(".+()|{}$")


@lru_cache(maxsize=None)
def _compile_pattern(pat: str) -> tuple[str, object]:
    """把单个 .dockerignore 模式编译成 ``(matchType, payload)``。

    这是 ``moby/patternmatcher`` 里 ``Pattern.compile()`` 的忠实移植 ——
    **不要**用 fnmatch 或 .gitignore 的直觉替代它,两者的差异恰恰是本脚本
    过去给出假绿的原因(见 is_excluded 的说明)。

    matchType 的四种取值与上游一致:``exact`` / ``prefix`` / ``suffix`` /
    ``regexp``。
    """
    reg = "^"
    detected = "exact"          # 与 Go 一致:默认按精确匹配
    pos = 0
    iter_idx = 0                # Go 里 `for i := 0; ...; i++` 的 i
    n = len(pat)

    while pos < n:
        ch = pat[pos]
        pos += 1

        if ch == "*":
            if pos < n and pat[pos] == "*":
                # 某个形态的 "**"
                pos += 1
                # "**/" 视作 "**":把那个 "/" 吃掉
                if pos < n and pat[pos] == "/":
                    pos += 1

                if pos >= n:
                    # 结尾的 "**":与 .gitignore 对齐,接受一切
                    if detected == "exact":
                        detected = "prefix"
                    else:
                        reg += ".*"
                        detected = "regexp"
                else:
                    # 中间的 "**":允许任意层数(含 0 层 ——
                    # 所以 `**/*.md` 也能匹配根目录的 README.md)
                    reg += "(.*/)?"
                    detected = "regexp"

                if iter_idx == 0:
                    detected = "suffix"
            else:
                # 单个 "*":任意字符,但**不含 "/"**
                reg += "[^/]*"
                detected = "regexp"
        elif ch == "?":
            reg += "[^/]"
            detected = "regexp"
        elif ch in _ESCAPE_IN_REGEX:
            reg += "\\" + ch
        elif ch == "\\":
            if pos < n:
                reg += "\\" + pat[pos]
                pos += 1
                detected = "regexp"
            else:
                reg += "\\"
        elif ch == "[" or ch == "]":
            reg += ch
            detected = "regexp"
        else:
            reg += ch

        iter_idx += 1

    if detected == "regexp":
        return "regexp", re.compile(reg + "$")
    return detected, pat


def _match_one(rel: str, mtype: str, payload: object) -> bool:
    """单个模式对单条路径的匹配,对应上游 ``Pattern.match()``。"""
    if mtype == "exact":
        return rel == payload
    if mtype == "prefix":
        return rel.startswith(payload[:-2])          # 去掉结尾的 "**"
    if mtype == "suffix":
        suffix = payload[2:]                         # 去掉开头的 "**"
        if rel.endswith(suffix):
            return True
        # `**/foo` 也匹配裸的 `foo`
        return suffix.startswith("/") and rel == suffix[1:]
    if mtype == "regexp":
        return payload.match(rel) is not None
    return False


def is_excluded(rel: str, pats: list[tuple[bool, str]]) -> bool:
    """等价于 ``moby/patternmatcher`` 的 ``MatchesOrParentMatches()``。

    ⚠️ **别用 fnmatch / .gitignore 的直觉来理解这里。**

    Docker 的 ``*`` 与 ``?`` **不跨 "/"**(Go 的 filepath.Match 语义),
    所以:

    - ``*.md``   只匹配**根目录**的 .md,``deploy/x.md`` / ``liz_bot/x.md``
      都**不会**被排除;
    - ``__pycache__`` 是精确匹配,只排除**根目录**那一个,
      ``liz_bot/__pycache__`` 照进不误;
    - 想匹配任意层级,必须显式写 ``**/``。``**/`` 也能匹配 0 层
      (即根目录本身),所以 ``**/*.md`` 一次覆盖根目录与所有子目录。

    本函数原先用 ``fnmatch`` + "目录名出现在任意层级" 的近似实现,
    那是 **.gitignore 的语义**,比真实 Docker **宽松** —— 方向恰好是最危险
    的那一侧:它会把"其实会进镜像"的文件报成已排除,于是漏检。2026-09-25
    实测确认 ``Assembly-CSharp``(裸名)在真实 Docker 下**匹配不到**
    ``maimai/baogao/Assembly-CSharp``,而旧实现报"已排除"。

    后匹配者胜(与 Docker 一致);反排除(``!``)只在已匹配后才生效。
    """
    matched = False
    parent = posixpath.dirname(rel)
    parent_dirs = parent.split("/") if parent else []

    for negate, pat in pats:
        mtype, payload = _compile_pattern(pat)
        # 与上游一致:命中反排除而尚未匹配、或已匹配又遇到普通规则,都跳过
        if negate != matched:
            continue

        m = _match_one(rel, mtype, payload)
        if not m:
            # 模式命中任一祖先目录 => 整个子树被排除(所以排除目录很快)
            for i in range(len(parent_dirs)):
                m = _match_one("/".join(parent_dirs[: i + 1]), mtype, payload)
                if m:
                    break

        if m:
            matched = not negate
    return matched


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

        # 文档一律不进镜像（用户 2026-09-25 的要求）。这条同样**不依赖 HEAD**：
        # 直接拿 .dockerignore 的规则去匹配。于是"规则被删掉"或"写成裸 `*.md`"
        # 都会立刻失败 —— 后者只排除根目录那几份，下面的子目录样例会漏出来。
        for rel in ("README.md",
                    "liz_bot/_已移除功能_舞萌发包.md",
                    "三套SDGB实现对比.md"):
            check(is_excluded(rel, pats), f".dockerignore 排除了 {rel}")

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

    # -------------------------------------- compose 与 Dockerfile / 卷 的一致性
    #
    # 本节**刻意不依赖 git**（直接读工作区），理由和上面那条「.dockerignore
    # 未排除只读数据」一样：这些是"改错了也不会立刻报错"的地方 ——
    # compose 里 LIZ_DATA_DIR 与卷挂载点写成两个路径，机器人照常启动、
    # 照常回消息，只是日志与会话历史落进了容器层，重启就没。
    #
    # 另有一条是**安全**相关：compose 与 deploy/.env 必须被 .dockerignore
    # 排除。镜像层是可被拉取的，AppSecret 写进去等于公开发布。
    print()
    print("=" * 72)
    print("F. deploy/docker-compose.yml 与 Dockerfile / 卷 的一致性")
    print("=" * 72)

    compose_path = REPO / "deploy" / "docker-compose.yml"
    if not compose_path.exists():
        check(False, "deploy/docker-compose.yml 存在（§3.6 的容器部署入口）")
    else:
        check(True, "deploy/docker-compose.yml 存在")
        compose = None
        if _yaml is None:
            check(False, "PyYAML 可用（解析 compose 需要）", "pip install PyYAML")
        else:
            try:
                compose = _yaml.safe_load(compose_path.read_text(encoding="utf-8"))
                check(True, "compose 文件能被 YAML 解析")
            except Exception as exc:  # noqa: BLE001 - 解析失败原因要原样报出来
                check(False, "compose 文件能被 YAML 解析",
                      f"{type(exc).__name__}: {exc}")

        if isinstance(compose, dict):
            svc = (compose.get("services") or {}).get("liz-bot") or {}
            check(bool(svc), "存在 liz-bot 服务")
            build = svc.get("build") or {}
            env_map = svc.get("environment") or {}

            # build.context 写错成 "." 的话，构建上下文会变成 deploy/，
            # 镜像里就没有 run.py —— 而且要到容器启动才炸。
            ctx = str(build.get("context") or "").strip()
            ctx_abs = (compose_path.parent / ctx).resolve()
            check(ctx_abs == REPO, f"build.context 指回仓库根（{ctx!r}）", str(ctx_abs))
            df = str(build.get("dockerfile") or "").strip()
            check(bool(df) and (REPO / df).exists(), f"Dockerfile 存在（{df!r}）")

            # build.args 的每个键都必须在 Dockerfile 里「既声明、又真的用到」。
            # 两种情况都是**静默**失败：compose 传了值、Dockerfile 不接，
            # 构建照常成功，只是用的还是默认值 —— 表现是"我明明配了镜像源，
            # 构建还是慢"。只查 ARG 声明不够：`ARG FOO` 写了却从没出现 `$FOO`
            # 时，值同样被丢掉，而只看声明的断言会放过它。
            # （build.args 也允许写成列表形式，这里只处理字典形式。）
            args = build.get("args") or {}
            if isinstance(args, dict) and args and (REPO / df).exists():
                dockerfile = (REPO / df).read_text(encoding="utf-8")

                def _declared(k):
                    return re.search(rf"^\s*ARG\s+{re.escape(k)}\b", dockerfile, re.M)

                def _used(k):
                    # $FOO 或 ${FOO} 都算用到；\b 防止 PIP_INDEX 被
                    # PIP_INDEX_URL 之类的前缀命中（反之亦然）。
                    return re.search(rf"\$\{{?{re.escape(k)}\b", dockerfile)

                undeclared = [k for k in args if not _declared(k)]
                unused = [k for k in args if _declared(k) and not _used(k)]
                check(not undeclared,
                      f"compose build.args 的 {len(args)} 个键都有 ARG 声明",
                      "缺声明:" + ", ".join(undeclared))
                check(not unused,
                      f"compose build.args 的 {len(args)} 个键在 Dockerfile 里都真的被引用",
                      "声明了但没用:" + ", ".join(unused))

            # 挂载点 ↔ LIZ_DATA_DIR
            mounts = set()
            for v in (svc.get("volumes") or []):
                if isinstance(v, str) and ":" in v:
                    mounts.add(v.split(":")[1])
            data_dir = str(env_map.get("LIZ_DATA_DIR") or "").strip()
            check(data_dir in mounts,
                  f"LIZ_DATA_DIR({data_dir!r}) 与卷挂载点一致",
                  "挂载点:" + ", ".join(sorted(mounts)))

            # 探针打的是**容器内**端口，端口号来自 HEALTHZ_PORT，两边必须一致
            hz = str(env_map.get("HEALTHZ_PORT") or "").strip()
            pub = [p for p in (svc.get("ports") or []) if isinstance(p, str)]
            targets = {p.rsplit(":", 1)[-1] for p in pub}
            check(hz in targets, f"HEALTHZ_PORT({hz!r}) 与 ports 容器侧一致",
                  "ports:" + ", ".join(pub))

            # 凭据只能来自 env_file —— 这个文件是要进 git 的
            env_files = svc.get("env_file") or []
            if isinstance(env_files, str):
                env_files = [env_files]
            env_files = [str(f) for f in env_files]
            check(bool(env_files), "凭据走 env_file 而非硬编码")
            # 比 resolve 后的路径而不是字符串：`./.env` / `.env` / `deploy/.env`
            # 是同一个文件的三种写法，按字符串比会误报。
            env_paths = {(compose_path.parent / f).resolve() for f in env_files}
            check((compose_path.parent / ".env").resolve() in env_paths,
                  "env_file 指向 deploy/.env", ", ".join(env_files))
            check((compose_path.parent / ".env.example").exists(),
                  "deploy/.env.example 模板存在（.env 被 gitignore 排除）")

            raw = compose_path.read_text(encoding="utf-8")
            hardcoded = [
                k for k in ("QQ_BOT_APPID", "QQ_BOT_SECRET")
                if re.search(rf"^\s*{k}\s*[:=]\s*\S", raw, re.M)
            ]
            check(not hardcoded, "compose 内无硬编码凭据", ", ".join(hardcoded))

    # compose 与 .env 都不能进镜像层（镜像是可被拉取的）
    check(is_excluded("deploy/docker-compose.yml", pats),
          ".dockerignore 排除了 deploy/docker-compose.yml")
    check(is_excluded("deploy/.env", pats),
          ".dockerignore 排除了 deploy/.env")

    # ------------------------------------------ 工作区本地文件会不会漏进上下文
    #
    # **本脚本原先的结构性盲区。** A–E 节用 `git archive <ref>` 复现构建上下文，
    # 而 git archive **不包含**被 .gitignore 排除的文件。但真实的 `docker build`
    # 用的是**工作区**，只被 .dockerignore 过滤 —— 于是「gitignore 了、却忘了
    # dockerignore」的文件在本脚本里**完全看不见**，却实实在在会进镜像。
    #
    # 2026-09-25 实测踩到：maimai/DLL/（2358 个文件 / 14.4MB 的反编译游戏代码）
    # 正是这个状态 —— 本脚本全绿，而构建上下文会平白多传 14.4MB、镜像层里
    # 还会带上它。同类的还有 .workbuddy-ai/ 与 *.bundle。
    #
    # 本节直接走工作区，按 .dockerignore 的语义**剪枝**（与 Docker 一致，
    # 所以很快），找出「既不在 git 里、也没被 .dockerignore 排除」的文件。
    print()
    print("=" * 72)
    print("G. 工作区里有没有会漏进构建上下文的本地文件")
    print("=" * 72)

    # 已知例外：本地有、且**故意**要进镜像的。往里加东西必须写清理由。
    LOCAL_ALLOW = {
        "liz_bot/emoji": "pic_haddler 实际读取的表情包目录（.dockerignore 刻意保留）",
    }
    SIZE_FLOOR = 64 * 1024        # 小于这个体积不报，避免噪音

    tracked_set = set(tracked)
    leaks: list[tuple[int, str]] = []

    def _scan(d: Path, rel: str) -> None:
        try:
            entries = sorted(d.iterdir())
        except OSError:
            return
        for e in entries:
            r = f"{rel}/{e.name}" if rel else e.name
            if is_excluded(r, pats):
                continue                      # 与 Docker 一样：整目录剪枝
            if e.is_dir():
                _scan(e, r)
            elif e.is_file():
                if r in tracked_set:
                    continue                  # 在 git 里 = 本来就该进镜像
                if any(r == a or r.startswith(a + "/") for a in LOCAL_ALLOW):
                    continue
                if e.stat().st_size >= SIZE_FLOOR:
                    leaks.append((e.stat().st_size, r))

    for top in sorted(REPO.iterdir()):
        if top.name == ".git" or is_excluded(top.name, pats):
            continue
        if top.is_dir():
            _scan(top, top.name)
        elif top.is_file():
            if top.name in tracked_set:
                continue
            if top.stat().st_size >= SIZE_FLOOR:
                leaks.append((top.stat().st_size, top.name))

    leaks.sort(reverse=True)
    check(not leaks,
          f"没有漏网的本地文件（已剪枝 .dockerignore 排除项，"
          f"例外:{', '.join(LOCAL_ALLOW)}）",
          "会进镜像:" + ", ".join(f"{r}({sz // 1024}KB)" for sz, r in leaks))

    # ------------------------------------------------ 匹配语义自检
    #
    # 上面每一节的可信度都建立在一个前提上：本脚本的 .dockerignore 匹配与
    # 真实 Docker 一致。2026-09-25 之前它**不是** —— 旧实现按 .gitignore 语义
    # 做近似（fnmatch + "裸名匹配任意层级"），比 Docker **宽松**，会把"其实会
    # 进镜像"的文件报成已排除。方向恰好是最危险的那一侧，而且它让本脚本对
    # .dockerignore 自身的错误**完全失明**（`Assembly-CSharp` 裸名空转了很久
    # 都没被发现）。
    #
    # 所以这里把匹配器的行为钉死。前六条是 **Docker 官方文档** `.dockerignore`
    # 一节的原文例子，其余是本项目实际踩到的场景。改动 _compile_pattern /
    # is_excluded 时，这一节必须保持全绿。
    print()
    print("=" * 72)
    print("H. .dockerignore 匹配语义自检（对齐 moby/patternmatcher）")
    print("=" * 72)

    semantics: list[tuple[str, list[str], bool, str]] = [
        # --- Docker 官方文档 .dockerignore 一节的例子（原文语义）---
        ("somedir/temporary.txt", ["*/temp*"], True, "官方例:排除子目录里的 temp*"),
        ("somedir/temp", ["*/temp*"], True, "官方例:目录也一并排除"),
        ("temporary.txt", ["*/temp*"], False, "官方例:根目录的不匹配(* 不跨 /)"),
        ("somedir/subdir/temporary.txt", ["*/*/temp*"], True, "官方例:两层"),
        ("tempa", ["temp?"], True, "官方例:temp? 匹配根目录"),
        ("somedir/tempa", ["temp?"], False, "官方例:temp? 够不到子目录"),
        # --- 本项目实际踩到的 ---
        ("README.md", ["**/*.md"], True, "**/*.md 覆盖根目录（0 层）"),
        ("liz_bot/x.md", ["**/*.md"], True, "**/*.md 覆盖子目录"),
        ("liz_bot/x.md", ["*.md"], False, "裸 *.md 够不到子目录 —— 别写这个"),
        ("maimai/baogao/Assembly-CSharp/A.cs", ["**/Assembly-CSharp"], True,
         "**/Assembly-CSharp 能防改名逃逸"),
        ("maimai/baogao/Assembly-CSharp/A.cs", ["Assembly-CSharp"], False,
         "裸 Assembly-CSharp 是空转（2026-09-25 修掉的坑）"),
        ("liz_bot/__pycache__/x.pyc", ["**/__pycache__"], True, "**/ 覆盖子目录"),
        ("liz_bot/__pycache__/x.pyc", ["__pycache__"], False, "裸 __pycache__ 是空转"),
        ("_tools/x.py", ["_tools"], True, "根目录裸名精确匹配（正常用法）"),
        ("liz_bot/divingfish_songs/music_data.json", ["**/*.json"], True,
         "**/*.json 会误伤只读数据 —— 只读数据那节才反复警告"),
        ("liz_bot/divingfish_songs/music_data.json", ["*.json"], False,
         "裸 *.json 恰好够不到（是巧合，不是保证）"),
    ]

    def _norm(raw_pats: list[str]) -> list[tuple[bool, str]]:
        return [(p.startswith("!"), p.lstrip("!").strip().lstrip("/").rstrip("/"))
                for p in raw_pats]

    bad_sem = [
        f"{rel} + {raw} -> {is_excluded(rel, _norm(raw))}(期望 {want}):{why}"
        for rel, raw, want, why in semantics
        if is_excluded(rel, _norm(raw)) != want
    ]
    check(not bad_sem,
          f"匹配语义 {len(semantics)} 例与 moby/patternmatcher 一致",
          "不符合:" + "; ".join(bad_sem))

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
