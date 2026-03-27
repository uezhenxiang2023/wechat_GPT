import base64
import io

from PIL import Image

from common.utils import get_ark_sessions


def encode_image_content(image_path, image_file):
    """将图片组装为多模态会话消息块。"""
    with open(image_path, "rb") as file:
        base64_image = base64.b64encode(file.read()).decode("utf-8")
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{_get_image_mime_type(image_file)};base64,{base64_image}"
        }
    }


def encode_image(image_path, image_file):
    """将图片转为生成模型可用的 data URL。"""
    with open(image_path, "rb") as file:
        base64_image = base64.b64encode(file.read()).decode("utf-8")
    return f"data:{_get_image_mime_type(image_file)};base64,{base64_image}"


def process_image_files(file_cache):
    """处理图片缓存，返回按原顺序编码后的图片内容列表。"""
    if not file_cache:
        return []

    image_contents = []
    image_files = file_cache["files"]
    image_paths = file_cache["path"]
    for image_path, image_file in zip(image_paths, image_files):
        image_contents.append(encode_image_content(image_path, image_file))
    return image_contents


def size_calculator(files):
    """根据参考图推断最接近的豆包出图分辨率。"""
    ratio_resolutions = {
        1.0: "2048x2048",
        1.33: "2304x1728",
        0.75: "1728x2304",
        1.78: "2560x1440",
        0.56: "1440x2560",
        1.5: "2496x1664",
        0.67: "1664x2496",
        2.33: "3024x1296"
    }

    sizes_list = [file.size for file in files]
    best_size = sorted(sizes_list, key=lambda x: x[0] * x[1], reverse=True)[0]
    ratio = round(best_size[0] / best_size[1], 2)
    closest_ratio = min(ratio_resolutions.keys(), key=lambda x: abs(x - ratio))
    return ratio_resolutions[closest_ratio]


def size_calculator_from_data_urls(image_urls):
    """根据 session 中的 data URL 图片推断最接近的豆包出图分辨率。"""
    ratio_resolutions = {
        1.0: "2048x2048",
        1.33: "2304x1728",
        0.75: "1728x2304",
        1.78: "2560x1440",
        0.56: "1440x2560",
        1.5: "2496x1664",
        0.67: "1664x2496",
        2.33: "3024x1296"
    }

    sizes_list = [_decode_image_size(image_url) for image_url in image_urls]
    best_size = sorted(sizes_list, key=lambda x: x[0] * x[1], reverse=True)[0]
    ratio = round(best_size[0] / best_size[1], 2)
    closest_ratio = min(ratio_resolutions.keys(), key=lambda x: abs(x - ratio))
    return ratio_resolutions[closest_ratio]


def get_image_from_session(session_id):
    """从会话历史中倒序提取最近一条图片消息。"""
    session = get_ark_sessions().build_session(session_id)
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


def _get_image_mime_type(image_file):
    image_type = type(image_file).__name__
    if image_type == "PngImageFile":
        return "image/png"
    return "image/jpeg"


def _decode_image_size(image_url):
    b64_data = image_url.split(",", 1)[1]
    img = Image.open(io.BytesIO(base64.b64decode(b64_data)))
    return img.size
