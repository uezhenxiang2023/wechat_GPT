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
from common.model_status import model_state
from common.utils import get_chat_session_manager
from common.video_status import video_state
from config import conf

_user_chat_image_context = {}
_GEMINI_IMAGE_RATIO_MAP = {
    "1:1": 1.0,
    "1:4": 1 / 4,
    "4:1": 4 / 1,
    "1:8": 1 / 8,
    "8:1": 8 / 1,
    "2:3": 2 / 3,
    "3:2": 3 / 2,
    "3:4": 3 / 4,
    "4:3": 4 / 3,
    "4:5": 4 / 5,
    "5:4": 5 / 4,
    "9:16": 9 / 16,
    "16:9": 16 / 9,
    "21:9": 21 / 9,
}
_GEMINI_VIDEO_RATIO_MAP = {
    "16:9": 16 / 9,
    "9:16": 9 / 16,
}


class GeminiVideoGenerationError(RuntimeError):
    pass


class DownloadedGeminiVideo(BytesIO):
    def __init__(self, uri, video_bytes):
        super().__init__(video_bytes)
        self.uri = uri
        self.video_bytes = video_bytes


def get_paid_client(api_key):
    return genai.Client(api_key=api_key)


def get_user_image_chat(session_id, image_model, *, paid_client, safety_settings, aspect_ratio=None):
    image_settings = get_gemini_image_settings_for_session(session_id, aspect_ratio)
    img_config = GenerateContentConfig(
        safety_settings=safety_settings,
        response_modalities=["TEXT", "Image"],
        image_config=types.ImageConfig(
            aspect_ratio=image_settings["aspect_ratio"],
            image_size=image_settings["size"]
        )
    )
    img_config.tools = [{"google_search": {}}] if image_model == const.GEMINI_3_PRO_IMAGE_PREVIEW else None
    logger.info(
        f"[{image_model.upper()}] create image chat, session_id={session_id}, "
        f"image_size={image_settings['size']}, aspect_ratio={image_settings['aspect_ratio']}"
    )
    return paid_client.chats.create(model=image_model, config=img_config)


def get_image_from_session(session_id):
    context = get_image_context_from_session(session_id)
    return context["images"]


def get_image_context_from_session(session_id):
    session_manager = get_chat_session_manager(session_id) or _gemini_sessions
    session = session_manager.build_session(session_id)
    for idx in range(len(session.messages) - 1, -1, -1):
        msg = session.messages[idx]
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        images = _extract_image_urls_from_content(content)
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


def _extract_image_urls_from_content(content):
    images = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "image_url":
            image_url = item.get("image_url", {}).get("url")
            if image_url:
                images.append(image_url)
    return images


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


def infer_gemini_aspect_ratio_from_images(images):
    sizes = [image.size for image in images if hasattr(image, "size")]
    return _infer_gemini_aspect_ratio_from_sizes(sizes)


def infer_gemini_aspect_ratio_from_data_urls(image_urls):
    sizes = [_decode_image_size(image_url) for image_url in image_urls]
    return _infer_gemini_aspect_ratio_from_sizes(sizes)


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


def generate_video(
    *,
    paid_client,
    session_id,
    video_model,
    prompt,
    image=None,
    last_image=None,
    ref_images=None,
    aspect_ratio=None,
    resolution=None,
    duration_seconds=None
):
    image_genai = convert_image_to_types(image) if image else None
    last_image_genai = convert_image_to_types(last_image) if last_image else None
    ref_images_obj = build_ref_images_obj(ref_images) if ref_images else None

    video_resolution = _normalize_gemini_video_resolution(
        video_model,
        resolution or video_state.get_video_resolution(session_id)
    )
    has_reference_images = bool(ref_images_obj)
    has_last_frame = bool(last_image_genai)
    video_duration = _normalize_gemini_video_duration(
        duration_seconds if duration_seconds is not None else video_state.get_video_duration(session_id),
        video_model,
        video_resolution,
        has_reference_images=has_reference_images,
        has_last_frame=has_last_frame,
    )
    gen_config = types.GenerateVideosConfig(
        number_of_videos=1,
        duration_seconds=video_duration,
        resolution=video_resolution,
        aspect_ratio=_normalize_gemini_video_aspect_ratio(aspect_ratio),
        person_generation="allow_all"
    )
    request_kwargs = {
        "model": video_model,
        "prompt": prompt,
        "config": gen_config,
    }

    if image_genai:
        logger.info(f"[{video_model}] Detected Start Image. Using image-to-video mode.")
        request_kwargs["image"] = image_genai
        gen_config.person_generation = "allow_adult"
        if last_image_genai:
            logger.info(f"[{video_model}] Detected Last Image. Using transition mode.")
            gen_config.last_frame = last_image_genai
        if ref_images_obj:
            logger.info(f"[{video_model}] Reference images dropped because start/end frame is present.")
            gen_config.reference_images = None
    elif ref_images_obj:
        logger.info(f"[{video_model}] Using reference-guided mode.")
        gen_config.reference_images = ref_images_obj
        gen_config.person_generation = "allow_adult"
    else:
        logger.info(f"[{video_model}] Text-to-video mode.")

    operation = paid_client.models.generate_videos(**request_kwargs)

    poll_interval_seconds = 10
    logger.info(f"[{video_model}] Waiting for video generation to complete...")
    while not operation.done:
        logger.info(f"[{video_model}] Polling task status, retry after {poll_interval_seconds} seconds")
        time.sleep(poll_interval_seconds)
        operation = paid_client.operations.get(operation)
    logger.info(f"[{video_model}] Video generation completed successfully.")

    response_payload = getattr(operation, "response", None)
    generated_videos = getattr(response_payload, "generated_videos", None)
    if not generated_videos and isinstance(response_payload, dict):
        generated_videos = response_payload.get("generated_videos")
    if not generated_videos:
        logger.error(f"[{video_model}] No generated videos found in operation response: {response_payload}")
        raise GeminiVideoGenerationError(_format_gemini_video_error(response_payload))

    first_video = generated_videos[0]
    res_video = getattr(first_video, "video", None)
    if res_video is None and isinstance(first_video, dict):
        res_video = first_video.get("video")
    if res_video is None:
        logger.error(f"[{video_model}] Invalid generated video item: {first_video}")
        raise GeminiVideoGenerationError("Veo 返回结果异常，请稍后重试。")

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


def get_gemini_image_settings(aspect_ratio=None):
    return {
        "size": _normalize_gemini_image_size(conf().get("image_create_size", "1K")),
        "aspect_ratio": _normalize_gemini_image_aspect_ratio(
            aspect_ratio or conf().get("image_aspect_ratio", "16:9")
        ),
    }


def get_gemini_image_settings_for_session(session_id, aspect_ratio=None):
    return {
        "size": _normalize_gemini_image_size(model_state.get_image_size(session_id)),
        "aspect_ratio": _normalize_gemini_image_aspect_ratio(
            aspect_ratio or conf().get("image_aspect_ratio", "16:9")
        ),
    }


def _normalize_gemini_image_size(image_size: str) -> str:
    normalized = str(image_size).strip().upper()
    if normalized in {"512", "1K", "2K", "4K"}:
        return normalized
    logger.warning(f"[GeminiImage] invalid image_size={image_size}, fallback to 1K")
    return "1K"


def _normalize_gemini_image_aspect_ratio(aspect_ratio: str) -> str:
    normalized = str(aspect_ratio).strip()
    if normalized in _GEMINI_IMAGE_RATIO_MAP:
        return normalized
    logger.warning(f"[GeminiImage] invalid aspect_ratio={aspect_ratio}, fallback to 16:9")
    return "16:9"


def _normalize_gemini_video_aspect_ratio(aspect_ratio: str | None) -> str:
    normalized = str(aspect_ratio or conf().get("image_aspect_ratio", "16:9")).strip()
    if normalized in _GEMINI_VIDEO_RATIO_MAP:
        return normalized

    image_ratio = _GEMINI_IMAGE_RATIO_MAP.get(normalized)
    if image_ratio is None:
        logger.warning(f"[GeminiVideo] invalid aspect_ratio={aspect_ratio}, fallback to 16:9")
        return "16:9"
    return "16:9" if image_ratio >= 1 else "9:16"


def _normalize_gemini_video_resolution(video_model: str, resolution: str | None) -> str:
    normalized = str(resolution or "720p").strip().lower()
    allowed = _get_allowed_gemini_video_resolutions(video_model)
    if normalized in allowed:
        return normalized
    fallback = "720p"
    logger.warning(f"[{video_model}] invalid resolution={resolution}, fallback to {fallback}")
    return fallback


def _get_allowed_gemini_video_resolutions(video_model: str) -> set[str]:
    if video_model in {const.VEO_31, const.VEO_31_FAST}:
        return {"720p", "1080p", "4k"}
    if video_model == const.VEO_31_LITE:
        return {"720p", "1080p"}
    return {"720p", "1080p"}


def _normalize_gemini_video_duration(
    duration,
    video_model: str,
    resolution: str,
    *,
    has_reference_images: bool = False,
    has_last_frame: bool = False,
) -> int:
    if _must_use_eight_second_duration(
        video_model,
        resolution,
        has_reference_images=has_reference_images,
        has_last_frame=has_last_frame,
    ):
        return 8

    allowed_durations = _get_allowed_gemini_video_durations(video_model)
    fallback = min(allowed_durations)
    try:
        value = int(duration or fallback)
    except (TypeError, ValueError):
        logger.warning(f"[GeminiVideo] invalid duration={duration}, fallback to {fallback}")
        return fallback
    if value in allowed_durations:
        return value
    logger.warning(f"[GeminiVideo] invalid duration={duration}, fallback to {fallback}")
    return fallback


def get_gemini_video_settings(
    video_model: str,
    *,
    resolution=None,
    duration=None,
    has_reference_images: bool = False,
    has_last_frame: bool = False,
) -> dict:
    normalized_resolution = _normalize_gemini_video_resolution(video_model, resolution)
    normalized_duration = _normalize_gemini_video_duration(
        duration,
        video_model,
        normalized_resolution,
        has_reference_images=has_reference_images,
        has_last_frame=has_last_frame,
    )
    return {
        "resolution": normalized_resolution,
        "duration_seconds": normalized_duration,
    }


def _infer_gemini_aspect_ratio_from_sizes(sizes):
    if not sizes:
        return None
    best_size = sorted(sizes, key=lambda size: size[0] * size[1], reverse=True)[0]
    ratio = round(best_size[0] / best_size[1], 4)
    return min(_GEMINI_IMAGE_RATIO_MAP, key=lambda key: abs(_GEMINI_IMAGE_RATIO_MAP[key] - ratio))


def _get_allowed_gemini_video_durations(video_model: str) -> set[int]:
    if video_model == const.VEO_2:
        return {5, 6, 8}
    return {4, 6, 8}


def _must_use_eight_second_duration(
    video_model: str,
    resolution: str,
    *,
    has_reference_images: bool = False,
    has_last_frame: bool = False,
) -> bool:
    if has_reference_images:
        return True
    if has_last_frame:
        return True
    if video_model in {const.VEO_3, const.VEO_3_FAST, const.VEO_31, const.VEO_31_FAST, const.VEO_31_LITE} and resolution == "1080p":
        return True
    if video_model in {const.VEO_3, const.VEO_3_FAST, const.VEO_31, const.VEO_31_FAST} and resolution == "4k":
        return True
    return False


def _decode_image_size(image_url):
    return data_url_to_pil_image(image_url).size


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
