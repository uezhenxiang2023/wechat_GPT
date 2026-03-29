import io
import os
import requests, base64

from urllib.parse import urlparse
from PIL import Image


def fsize(file):
    if isinstance(file, io.BytesIO):
        return file.getbuffer().nbytes
    elif isinstance(file, str):
        return os.path.getsize(file)
    elif hasattr(file, "seek") and hasattr(file, "tell"):
        pos = file.tell()
        file.seek(0, os.SEEK_END)
        size = file.tell()
        file.seek(pos)
        return size
    else:
        raise TypeError("Unsupported type")


def compress_imgfile(file, max_size):
    if fsize(file) <= max_size:
        return file
    file.seek(0)
    img = Image.open(file)
    rgb_image = img.convert("RGB")
    quality = 95
    while True:
        out_buf = io.BytesIO()
        rgb_image.save(out_buf, "JPEG", quality=quality)
        if fsize(out_buf) <= max_size:
            return out_buf
        quality -= 5


def split_string_by_utf8_length(string, max_length, max_split=0):
    encoded = string.encode("utf-8")
    start, end = 0, 0
    result = []
    while end < len(encoded):
        if max_split > 0 and len(result) >= max_split:
            result.append(encoded[start:].decode("utf-8"))
            break
        end = min(start + max_length, len(encoded))
        # 如果当前字节不是 UTF-8 编码的开始字节，则向前查找直到找到开始字节为止
        while end < len(encoded) and (encoded[end] & 0b11000000) == 0b10000000:
            end -= 1
        result.append(encoded[start:end].decode("utf-8"))
        start = end
    return result


def get_path_suffix(path):
    path = urlparse(path).path
    return os.path.splitext(path)[-1].lstrip('.')

def url_to_base64(url: str) -> str:
        """下载 URL 内容并转为 base64 字符串"""
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return base64.b64encode(response.content).decode('utf-8')

# utils.py
def get_ark_sessions():
    from bot.ark.volcengine_ark_bot import _ark_sessions
    return _ark_sessions


def get_chat_session_manager(session_id):
    try:
        from bridge.bridge import Bridge

        bot = Bridge(session_id).get_bot("chat")
        sessions = getattr(bot, "sessions", None)
        if sessions is not None:
            return sessions
    except Exception:
        pass
    return get_ark_sessions()


def get_image_urls_from_session(session_id, session_manager=None):
    session_manager = session_manager or get_chat_session_manager(session_id)
    session = session_manager.build_session(session_id)
    for msg in reversed(session.messages):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        images = [
            item["image_url"]["url"]
            for item in content
            if item.get("type") == "image_url"
        ]
        if images:
            return images
    return []
