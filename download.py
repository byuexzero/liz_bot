import requests
import os
import sys
from pathlib import Path
from tqdm import tqdm
from urllib3.exceptions import InsecureRequestWarning

# 关闭 SSL 不安全请求警告（避免终端刷屏）
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

# GitHub 镜像源配置（优先级从高到低）
GITHUB_MIRRORS = [
    "https://github.com.cnpmjs.org",  # 淘宝镜像（推荐）
    "https://github.com.gitmirror.com",  # 中科院镜像
    "https://hub.fastgit.xyz"  # FastGit 镜像（备用）
]


def download_github_resource(
        raw_url: str,
        save_path: str | Path = None,
        chunk_size: int = 8192,
        timeout: int = 60,
        proxies: dict = None
) -> Path:
    """
    下载 GitHub 资源（文件/仓库ZIP包），自动使用镜像源并解决SSL验证问题

    :param raw_url: GitHub 原始资源URL（支持仓库ZIP/单个文件）
                    示例1（仓库ZIP）: https://github.com/CrazyKidCN/maimaiDX-CN-songs-database/archive/refs/heads/main.zip
                    示例2（单个文件）: https://raw.githubusercontent.com/CrazyKidCN/maimaiDX-CN-songs-database/main/songs.json
    :param save_path: 保存路径（默认自动提取文件名，保存到当前目录）
    :param chunk_size: 下载块大小（默认8192字节）
    :param timeout: 请求超时时间（默认60秒）
    :param proxies: 代理配置（可选，示例: {"http": "http://127.0.0.1:1080", "https": "http://127.0.0.1:1080"}）
    :return: 保存文件的绝对路径
    """

    # 1. 处理URL，替换为镜像源
    def replace_to_mirror(url: str) -> tuple[str, str]:
        """替换URL为镜像源，返回(镜像URL, 镜像名称)"""
        for mirror in GITHUB_MIRRORS:
            if "raw.githubusercontent.com" in url:
                # 处理 raw 文件镜像（不同镜像的raw路径规则）
                mirror_url = url.replace("raw.githubusercontent.com", f"{mirror.replace('https://', '')}/raw")
                return mirror_url, mirror.split("//")[-1]
            elif "github.com" in url:
                # 处理仓库ZIP包镜像
                mirror_url = url.replace("https://github.com", mirror)
                return mirror_url, mirror.split("//")[-1]
        return url, "官方源"

    # 2. 自动生成保存路径
    if save_path is None:
        # 提取文件名（处理URL中的特殊字符）
        file_name = Path(raw_url).name
        # 处理raw文件的特殊情况（避免文件名异常）
        if "raw.githubusercontent.com" in raw_url:
            file_name = raw_url.split("/")[-1]
        save_path = Path.cwd() / file_name
    save_path = Path(save_path).absolute()

    # 3. 尝试不同镜像源下载（一个失败自动切下一个）
    download_success = False
    error_messages = []

    for mirror_url, mirror_name in [replace_to_mirror(raw_url)] + [(raw_url, "官方源")]:
        try:
            print(f"📥 尝试使用 [{mirror_name}] 下载: {mirror_url}")

            # 发送请求（跳过SSL验证 + 支持断点续传）
            headers = {}
            downloaded_size = 0
            # 检查是否已有部分下载文件，实现断点续传
            if save_path.exists() and os.path.getsize(save_path) > 0:
                downloaded_size = os.path.getsize(save_path)
                headers["Range"] = f"bytes={downloaded_size}-"
                print(f"🔄 检测到已下载 {downloaded_size / 1024 / 1024:.2f}MB，开始断点续传...")

            response = requests.get(
                mirror_url,
                stream=True,
                verify=False,  # 解决SSL证书验证问题
                timeout=timeout,
                headers=headers,
                proxies=proxies or {}
            )
            response.raise_for_status()  # 抛出HTTP错误（4xx/5xx）

            # 获取文件总大小
            if "Content-Length" in response.headers:
                total_size = int(response.headers["Content-Length"]) + downloaded_size
            else:
                total_size = None

            # 初始化进度条
            progress_bar = tqdm(
                total=total_size,
                initial=downloaded_size,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                desc=f"💾 保存至: {save_path.name}",
                leave=True,
                ncols=80
            )

            # 写入文件（断点续传模式：ab 追加写入）
            mode = "ab" if downloaded_size > 0 else "wb"
            with open(save_path, mode) as f:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        progress_bar.update(len(chunk))
            progress_bar.close()

            # 验证文件完整性（可选）
            if total_size and os.path.getsize(save_path) != total_size:
                raise Exception(f"文件下载不完整，预期 {total_size} 字节，实际 {os.path.getsize(save_path)} 字节")

            print(f"✅ 下载成功！文件保存至: {save_path}")
            download_success = True
            break

        except Exception as e:
            err_msg = f"❌ [{mirror_name}] 下载失败: {str(e)}"
            error_messages.append(err_msg)
            print(err_msg)
            # 删除损坏的文件
            if save_path.exists() and os.path.getsize(save_path) == 0:
                save_path.unlink()
            continue

    if not download_success:
        raise Exception(f"\n所有镜像源下载失败！错误汇总:\n" + "\n".join(error_messages))

    return save_path


def download_github_repo(
        repo_owner: str,
        repo_name: str,
        branch: str = "main",
        save_dir: str | Path = None,
        **kwargs
) -> Path:
    """
    快捷下载GitHub仓库的ZIP包（封装上面的核心函数）

    :param repo_owner: 仓库所有者（示例: CrazyKidCN）
    :param repo_name: 仓库名称（示例: maimaiDX-CN-songs-database）
    :param branch: 分支名（默认main）
    :param save_dir: 保存目录（默认当前目录）
    :param kwargs: 传递给download_github_resource的其他参数（如proxies, timeout）
    :return: 保存的ZIP包路径
    """
    # 构造仓库ZIP包的原始URL
    raw_zip_url = f"https://github.com/{repo_owner}/{repo_name}/archive/refs/heads/{branch}.zip"
    # 生成保存路径
    if save_dir is None:
        save_dir = Path.cwd()
    save_path = Path(save_dir) / f"{repo_name}-{branch}.zip"
    # 调用核心下载函数
    return download_github_resource(raw_zip_url, save_path, **kwargs)


# -------------------------- 示例调用 --------------------------
if __name__ == "__main__":
    # 示例1：下载GitHub单个文件（如songs.json）
    try:
        file_url = "https://raw.githubusercontent.com/CrazyKidCN/maimaiDX-CN-songs-database/main/songs.json"
        download_github_resource(file_url, save_path="songs.json")
    except Exception as e:
        print(f"文件下载失败: {e}")
