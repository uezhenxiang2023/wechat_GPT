import base64
import os
import tempfile
import time
from io import BytesIO

from PIL import Image
from google import genai
from google.genai import types
from google.genai.types import Part, GenerateContentConfig

from bot.gemini.google_gemini_session import _gemini_sessions
from common import const
from common.log import logger
from common.video_status import video_state

_user_chat_image_context = {}


class DownloadedGeminiVideo(BytesIO):
    def __init__(self, uri, video_bytes):
        super().__init__(video_bytes)
        self.uri = uri
        self.video_bytes = video_bytes


def get_paid_client(api_key):
    return genai.Client(api_key=api_key)


def get_user_image_chat(session_id, image_model, *, paid_client, safety_settings):
    img_config = GenerateContentConfig(
        safety_settings=safety_settings,
        response_modalities=["TEXT", "Image"],
        image_config=types.ImageConfig(aspect_ratio="16:9")
    )
    img_config.tools = [{"google_search": {}}] if image_model == const.GEMINI_3_PRO_IMAGE_PREVIEW else None
    return paid_client.chats.create(model=image_model, config=img_config)


def get_image_from_session(session_id):
    context = get_image_context_from_session(session_id)
    return context["images"]


def get_image_context_from_session(session_id):
    session = _gemini_sessions.build_session(session_id)
    for idx in range(len(session.messages) - 1, -1, -1):
        msg = session.messages[idx]
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        images = [
            item["image_url"]["url"]
            for item in content
            if item.get("type") == "image_url"
        ]
        if images:
            prompt = ""
            for prev_idx in range(idx - 1, -1, -1):
                prev_msg = session.messages[prev_idx]
                prev_content = prev_msg.get("content")
                if prev_msg.get("role") != "user":
                    continue
                if isinstance(prev_content, str) and prev_content.strip():
                    prompt = prev_content.strip()
                    break
            return {
                "images": images,
                "prompt": prompt,
                "signature": _build_image_context_signature(images, prompt)
            }
    return {
        "images": [],
        "prompt": "",
        "signature": ""
    }


def should_inject_image_context(session_id, signature):
    if not signature:
        return False
    return _user_chat_image_context.get(session_id) != signature


def mark_image_context_injected(session_id, signature):
    if signature:
        _user_chat_image_context[session_id] = signature


def clear_image_context_marker(session_id):
    if session_id in _user_chat_image_context:
        del _user_chat_image_context[session_id]


def data_url_to_part(image_url):
    header, b64_data = image_url.split(",", 1)
    mime_type = header.split(":", 1)[1].split(";", 1)[0]
    return Part.from_bytes(data=base64.b64decode(b64_data), mime_type=mime_type)


def data_url_to_pil_image(image_url):
    _, b64_data = image_url.split(",", 1)
    image = Image.open(BytesIO(base64.b64decode(b64_data)))
    image.load()
    return image


def convert_image_to_types(image):
    image_format = image.format or "PNG"
    image_bytes_io = BytesIO()
    image.save(image_bytes_io, image_format)
    image_bytes = image_bytes_io.getvalue()
    mime_type = f"image/{image_format.lower()}"
    return types.Image(image_bytes=image_bytes, mime_type=mime_type)


def build_ref_images_obj(ref_images):
    ref_images_obj = []
    for ref_image in ref_images:
        ref_image_genai = convert_image_to_types(ref_image)
        ref_images_obj.append(
            types.VideoGenerationReferenceImage(
                image=ref_image_genai,
                reference_type="asset"
            )
        )
    return ref_images_obj


def generate_video(*, paid_client, session_id, video_model, prompt, image=None, last_image=None, ref_images=None):
    image_genai = convert_image_to_types(image) if image else None
    last_image_genai = convert_image_to_types(last_image) if last_image else None
    ref_images_obj = build_ref_images_obj(ref_images) if ref_images else None

    video_duration = 8 if video_state.get_video_resolution(session_id) == "1080p" else video_state.get_video_duration(session_id)
    video_resolution = video_state.get_video_resolution(session_id)
    gen_config = types.GenerateVideosConfig(
        number_of_videos=1,
        duration_seconds=video_duration,
        resolution=video_resolution,
        person_generation="allow_all"
    )
    gen_source = types.GenerateVideosSource(prompt=prompt)

    if image_genai:
        logger.info(f"[{video_model}] Detected Start Image. Using image-to-video mode.")
        gen_source.image = image_genai
        gen_config.person_generation = "allow_adult"
        if last_image_genai:
            logger.info(f"[{video_model}] Detected Last Image. Using transition mode.")
            gen_config.last_frame = last_image_genai
            gen_config.duration_seconds = 8
        if ref_images_obj:
            logger.info(f"[{video_model}] Reference images dropped because start/end frame is present.")
            gen_config.reference_images = None
    elif ref_images_obj:
        logger.info(f"[{video_model}] Using reference-guided mode.")
        gen_config.reference_images = ref_images_obj
        gen_config.duration_seconds = 8
        gen_config.person_generation = "allow_adult"
    else:
        logger.info(f"[{video_model}] Text-to-video mode.")

    operation = paid_client.models.generate_videos(
        model=video_model,
        source=gen_source,
        config=gen_config
    )

    logger.info(f"[{video_model}] Waiting for video generation to complete...")
    while not operation.done:
        time.sleep(10)
        operation = paid_client.operations.get(operation)
    logger.info(f"[{video_model}] Video generation completed successfully.")

    response_payload = getattr(operation, "response", None)
    generated_videos = getattr(response_payload, "generated_videos", None)
    if not generated_videos and isinstance(response_payload, dict):
        generated_videos = response_payload.get("generated_videos")
    if not generated_videos:
        logger.error(f"[{video_model}] No generated videos found in operation response: {response_payload}")
        raise RuntimeError(_format_gemini_video_error(response_payload))

    first_video = generated_videos[0]
    res_video = getattr(first_video, "video", None)
    if res_video is None and isinstance(first_video, dict):
        res_video = first_video.get("video")
    if res_video is None:
        logger.error(f"[{video_model}] Invalid generated video item: {first_video}")
        raise RuntimeError("Gemini video generation returned an invalid video item")

    paid_client.files.download(file=res_video)
    video_bytes = _read_downloaded_video_bytes(res_video)
    return DownloadedGeminiVideo(getattr(res_video, "uri", ""), video_bytes)


def extract_inline_image(response):
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return None, None
    parts = getattr(candidates[0].content, "parts", None) or []
    for part in parts:
        inline_data = getattr(part, "inline_data", None)
        if inline_data:
            return inline_data.mime_type, inline_data.data
    return None, None


def _build_image_context_signature(images, prompt):
    return f"{len(images)}|{images[0] if images else ''}|{prompt}"


def _read_downloaded_video_bytes(video_file):
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_file:
            temp_path = temp_file.name
        video_file.save(temp_path)
        with open(temp_path, "rb") as file:
            return file.read()
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


def _format_gemini_video_error(response_payload):
    error_text = str(response_payload)
    if "celebrity or their likenesses" in error_text:
        return "Veo 不支持基于包含名人或名人肖像的参考图生成视频，请改用纯文生视频，或换成不含名人肖像的参考图。"
    if "rai_media_filtered" in error_text or "filtered" in error_text:
        return "Veo 返回内容被安全策略过滤，请调整提示词或参考素材后重试。"
    return "Gemini video generation succeeded but returned no video payload"
