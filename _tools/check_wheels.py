"""预检 requirements.txt 在 Docker 构建时能否全部装上预编译 wheel。

为什么需要
----------
Dockerfile 基于 `python:3.10-slim`(Debian,glibc)。slim 镜像**没有编译器**,
我已在 Dockerfile 里装了 `build-essential` 兜底,但那会让构建慢几分钟。
更值得知道的是:**有没有包会触发源码编译** —— 以及**有没有包需要系统库**
(那种情况光装 build-essential 也不够,得再装 libxxx-dev)。

判定必须理解 wheel 标签的兼容规则,不能只看文件名里有没有 "cp310":

=========================================  ==================================
标签                                        对 CPython 3.10 / linux-amd64
=========================================  ==================================
``py3-none-any``                            兼容(纯 Python)
``cp37-abi3-manylinux…x86_64``              兼容 —— **稳定 ABI,向前兼容**
``cp310-cp310-manylinux…x86_64``            兼容
``cp310-cp310-musllinux…``                  不兼容(Debian 是 glibc 不是 musl)
``cp27-*`` / ``pp310-pypy…``                不兼容
=========================================  ==================================

> 踩过的坑:第一版只找文件名里的 ``cp310``,于是把 **pycryptodome 误判成
> "需编译"** —— 它发的是 ``cp37-abi3`` 稳定 ABI wheel,对 3.10 完全可用。
> 这类假警报会让人白装编译器、白等构建时间。

    python _tools/check_wheels.py
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.request

TARGET_PY = (3, 10)          # Dockerfile 里的 Python 版本
TARGET_ARCH = "x86_64"       # linux/amd64

# 来自 requirements.txt 的顶层依赖(含 PEP 508 版本约束)
REQUIREMENTS = [
    "qq-botpy>=1.2.1",
    "PyYAML>=6.0.3",
    "httpx>=0.28.1",
    "pycryptodome>=3.23.0",
    "pytz>=2025.2",
    "tqdm>=4.67.1",
    "python-dotenv>=1.0.0",
    "ijson>=3.4.0.post0",
    "aiohttp>=3.13.2",
    "requests>=2.32.5",
    "urllib3>=2.6.2",
    "qianfan>=0.4.12.3",
    "aiofiles>=25.1.0",
]

_SPEC = re.compile(r"^([A-Za-z0-9._-]+)\s*(.*)$")
_CP = re.compile(r"^cp3(\d+)$")


def fetch(name: str, tries: int = 5) -> dict:
    """查 PyPI。必须带重试 —— 这个网络环境偶发 SSL 掐断。"""
    last: Exception | None = None
    for i in range(tries):
        try:
            req = urllib.request.Request(
                f"https://pypi.org/pypi/{name}/json",
                headers={"User-Agent": "wheel-precheck"},
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except OSError as exc:                 # SSLError / URLError 都是 OSError
            last = exc
            time.sleep(2 ** i)
    raise last if last else RuntimeError("fetch failed")


def classify(files: list[dict]) -> tuple[str, str]:
    """返回 (结论, 说明)。结论 ∈ {pure, wheel, sdist-only}"""
    best: tuple[str, str] | None = None
    for f in files:
        if f["packagetype"] != "bdist_wheel":
            continue
        fname = f["filename"]
        parts = fname[:-4].split("-")          # 去掉 .whl
        if len(parts) < 5:
            continue
        py, abi, plat = parts[-3], parts[-2], parts[-1]

        # 1. 纯 Python:平台标签是 `any`,与架构、glibc 都无关 —— 必须先判,
        #    否则会被下面的 manylinux 过滤误杀(踩过:httpx/requests 等被误报需编译)。
        #    注意 python 标签可能是 `py2.py3` 或 `py310`,所以用 `in` 而不是
        #    `startswith("py3")` —— 后者会漏掉 `py2.py3-none-any`(pytz 就是这种)。
        if "py3" in py and abi == "none" and "any" in plat:
            return "pure", f"纯 Python({fname})"

        # 2. 平台必须是 linux/amd64 可用(排除 macos / win / musllinux)
        if not ("manylinux" in plat and TARGET_ARCH in plat):
            continue

        # 3. 稳定 ABI:cp3X-abi3 对更高的 3.Y 版本向前兼容
        if abi == "abi3":
            m = _CP.match(py)
            if m and int(m.group(1)) <= TARGET_PY[1]:
                best = best or ("wheel", f"稳定 ABI {py}-abi3({fname})")
                continue

        # 4. 精确匹配当前解释器
        if py == f"cp{TARGET_PY[0]}{TARGET_PY[1]}" and abi.startswith("cp310"):
            return "wheel", f"精确匹配 {py}-{abi}({fname})"

    if best:
        return best
    return "sdist-only", "无 linux/amd64 可用 wheel"


def main() -> None:
    print(f"目标环境:CPython {TARGET_PY[0]}.{TARGET_PY[1]} / linux-{TARGET_ARCH}(Debian glibc)")
    print()
    print(f"{'包':<18}{'结论':<12}依据")
    print("-" * 88)

    need_compile: list[str] = []
    failed: list[str] = []

    for spec in REQUIREMENTS:
        name = _SPEC.match(spec).group(1)
        try:
            data = fetch(name)
        except Exception as exc:                       # noqa: BLE001
            print(f"{name:<18}{'查询失败':<12}{exc}")
            failed.append(name)
            continue

        kind, detail = classify(data["urls"])
        if kind == "sdist-only":
            need_compile.append(name)
        label = {"pure": "纯 Python", "wheel": "有 wheel",
                 "sdist-only": "需编译"}[kind]
        print(f"{name:<18}{label:<12}{data['info']['version']}  {detail[:52]}")

    print()
    print("=" * 88)
    if failed:
        print(f"⚠ 查询失败 {len(failed)} 个:{', '.join(failed)}(网络问题,非依赖问题,可重跑)")
    if need_compile:
        print(f"⚠ 需要源码编译:{', '.join(need_compile)}")
        print("  → Dockerfile 里的 build-essential 是必需的,不要删。")
    else:
        print("✅ 全部依赖都有 linux/amd64 可直接安装的 wheel,不会触发源码编译。")
        print("   Dockerfile 里的 build-essential 目前是**冗余保险** —— 保留更稳")
        print("   (个别包对 glibc 版本有要求时可能回退到源码),想加快构建可以删掉。")
    print("=" * 88)

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
