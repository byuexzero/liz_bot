import os
import asyncio
import aiohttp
import time
from botpy.ext.cog_yaml import read
from botpy.message import GroupMessage, Message

# 全局配置
# 缓存AccessToken及过期时间
test_config = read(os.path.join(os.path.dirname(__file__), "./config/config.yaml"))
TOKEN_CACHE = {
    "access_token": "",
    "expires_at": 0  # 过期时间戳（秒）
}

# 全局文件列表（初始化逻辑，需放在函数外）
EMOJI_DIR = os.path.join(os.path.dirname(__file__), "emoji")
os.makedirs(EMOJI_DIR, exist_ok=True)
EMOJI_FILE_LIST = [
    {
        "filename": f,
        "local_path": os.path.join(EMOJI_DIR, f)
    }
    for f in os.listdir(EMOJI_DIR) if f.lower().endswith((".png", ".jpg"))
]


async def get_access_token():
    """获取/刷新AccessToken（鉴权核心）"""
    now = int(time.time())
    if TOKEN_CACHE["access_token"] and TOKEN_CACHE["expires_at"] > now:
        return TOKEN_CACHE["access_token"]

    async with aiohttp.ClientSession() as session:
        resp = await session.post(
            url="https://bots.qq.com/app/getAppAccessToken",
            headers={"Content-Type": "application/json"},
            json={"appId": test_config["appid"], "clientSecret": test_config["secret"]}
        )
        res = await resp.json()
        TOKEN_CACHE["access_token"] = res["access_token"]
        TOKEN_CACHE["expires_at"] = now + int(res["expires_in"]) - 60
        return res["access_token"]


async def upload_file_by_index(message: GroupMessage, file_index: int, srv_send_msg: bool = False):
    """
    按EMOJI_FILE_LIST索引上传本地文件到群聊（严格对齐官方接口）
    :param message: 群消息对象（获取group_openid）
    :param file_index: EMOJI_FILE_LIST列表索引（整数）
    :param srv_send_msg: 是否直接发送到群聊（占用主动频次，默认False）
    :return: 接口指定返回参数（file_uuid/file_info/ttl/id）
    """
    # 1. 校验索引有效性
    if not isinstance(file_index, int) or file_index < 0 or file_index >= len(EMOJI_FILE_LIST):
        raise ValueError(f"索引无效！有效范围：0~{len(EMOJI_FILE_LIST) - 1}")

    # 2. 根据索引获取本地文件信息
    file_info = EMOJI_FILE_LIST[file_index]
    filename = file_info["filename"]
    local_path = file_info["local_path"]

    # 3. 上传本地文件到腾讯媒体服务器，获取公网URL
    access_token = await get_access_token()
    media_url = "https://api.sgroup.qq.com/v2/media/upload"
    async with aiohttp.ClientSession() as session:
        with open(local_path, "rb") as f:
            form_data = aiohttp.FormData()
            form_data.add_field(
                "file", f,
                filename=filename,
                content_type="image/png" if filename.endswith(".png") else "image/jpeg"
            )
            media_resp = await session.post(
                url=media_url,
                headers={"Authorization": f"QQBot {access_token}"},
                data=form_data,
                params={"file_type": 1, "purpose": "group_file"}
            )
            media_res = await media_resp.json()
            if media_resp.status != 200:
                raise Exception(f"媒体服务器上传失败：{media_res}")
    public_url = media_res["url"]  # 提取公网URL

    # 4. 调用官方群聊文件接口（严格按文档传参）
    group_openid = message.group_openid
    group_file_url = f"https://api.sgroup.qq.com/v2/groups/{group_openid}/files"
    async with aiohttp.ClientSession() as session:
        file_resp = await session.post(
            url=group_file_url,
            headers={
                "Authorization": f"QQBot {access_token}",
                "Content-Type": "application/json"
            },
            json={
                "file_type": 1,  # 图片类型（png/jpg对应1）
                "url": public_url,  # 媒体服务器公网URL（必填）
                "srv_send_msg": srv_send_msg  # 是否直接发送（占用主动频次）
            }
        )
        file_res = await file_resp.json()
        if file_resp.status != 200:
            raise Exception(f"群文件接口调用失败：{file_res}")

    # 5. 返回接口指定的所有参数（严格对齐文档）
    return {
        "file_uuid": file_res.get("file_uuid"),
        "file_info": file_res.get("file_info"),
        "ttl": file_res.get("ttl"),
        "id": file_res.get("id")  # 仅srv_send_msg=True时返回
    }


# # ===================== 调用示例（带动态消息序号回复） =====================
# async def on_group_at_message_create(self, message: GroupMessage):
#     try:
#         # 示例1：调用索引0的文件（不主动发消息，仅获取file_info）
#         upload_res = await upload_file_by_index(message, file_index=0, srv_send_msg=False)
#
#         # 生成动态消息序号（避免重复）
#         msg_seq = int(time.time() * 1000) % 100000
#
#         # 被动回复富媒体消息（使用file_info）
#         await message.reply(
#             msg_type=7,
#             media=upload_res["file_info"],
#             msg_seq=msg_seq
#         )
#
#         # 示例2：调用索引1的文件（直接发送到群聊，占用主动频次）
#         # upload_res = await upload_file_by_index(message, file_index=1, srv_send_msg=True)
#
#     except Exception as e:
#         await message.reply(content=f"上传失败：{str(e)[:50]}", msg_type=0)




