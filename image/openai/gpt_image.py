import base64
import io
import re

import openai
from openai import OpenAI
from PIL import Image

from bot.bot import Bot
from bridge.context import Context
from bridge.reply import Reply, ReplyType
from common import const, memory
from common.aspect_ratio import parse_aspect_ratio_from_prompt
from common.log import logger
from common.model_status import model_state
from common.utils import get_chat_session_manager
from config import conf


class GPTImageBot(Bot):
    _SUPPORTED_SIZES = {"1024x1024", "1536x1024", "1024x1536", "auto"}
    _SUPPORTED_QUALITIES = {"low", "medium", "high", "auto"}
    _FLEXIBLE_SIZE_MODELS = {const.GPT_IMAGE_2}
    _PRESET_SIZE_MAP = {
        "1k": {
            "square": "1024x1024",
            "landscape": "1536x1024",
            "portrait": "1024x1536",
        },
        "2k": {
            "square": "2048x2048",
            "landscape": "2048x1152",
            "portrait": "1152x2048",
        },
        "4k": {
            "square": "2880x2880",
            "landscape": "3840x2160",
            "portrait": "2160x3840",
        },
    }
    _MIN_TOTAL_PIXELS = 655360
    _MAX_TOTAL_PIXELS = 8294400
    _MAX_EDGE = 3840
    _MAX_ASPECT_RATIO = 3.0
    _SEQUENTIAL_IMAGE_MAX_COUNT = 10
    _SEQUENTIAL_IMAGE_CN_NUM_MAP = {
        "两": 2,
        "俩": 2,
        "仨": 3,
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }

    def __init__(self):
        super().__init__()
        self.context_session_id = None
        self.client = OpenAI(
            api_key=conf().get("openai_api_key"),
            base_url=self._normalize_openai_base_url(conf().get("openai_api_base"))
        )

    def reply(self, query, context: Context = None) -> Reply:
        model = "gpt-image"
        try:
            session_id = context["session_id"]
            self.context_session_id = session_id
            model = model_state.get_image_model(session_id)
            session_manager = get_chat_session_manager(session_id)
            logger.info(f"[{model.upper()}] query={query}, requester={session_id}")
            session_manager.session_query(query, session_id)

            request_meta = self._build_request_meta(query, session_id, model)
            logger.info(
                f"[{model.upper()}] request summary: mode={request_meta['mode']}, "
                f"reference_count={request_meta['reference_count']}, "
                f"aspect_ratio={request_meta['aspect_ratio']}, image_size={request_meta['size']}, "
                f"quality={request_meta['quality']}"
            )

            image_count = self._parse_sequential_image_count_from_prompt(query) or 1
            if image_count > 1:
                logger.info(f"[{model.upper()}] 从 prompt 中解析到组图数量: {image_count}")

            response = self._create_or_edit_image(
                model=model,
                prompt=query,
                size=request_meta["size"],
                quality=request_meta["quality"],
                images=request_meta["images"],
                image_count=image_count
            )
            image_results = self._extract_images(response, model)
            if not image_results:
                raise ValueError("OpenAI image response missing image payload")

            try:
                for image_item in image_results:
                    session_manager.session_inject_media(
                        session_id=session_id,
                        media_type="image",
                        data=image_item["base64_data"],
                        source_model=model,
                        mime_type=image_item["mime_type"]
                    )
                logger.info(
                    f"[{model.upper()}] image injected to session, model={model}, "
                    f"session_id={session_id}, image_count={len(image_results)}"
                )
            except Exception as e:
                logger.warning(f"[{model.upper()}] failed to inject image to session: {e}")

            if len(image_results) == 1:
                return Reply(ReplyType.IMAGE, image_results[0]["image_storage"])

            remote_urls = [item["remote_url"] for item in image_results if item.get("remote_url")]
            if len(remote_urls) == len(image_results):
                return Reply(ReplyType.IMAGE_URL, remote_urls)

            logger.warning(f"[{model.upper()}] multi-image response has no remote urls, fallback to first image")
            return Reply(ReplyType.IMAGE, image_results[0]["image_storage"])
        except Exception as e:
            logger.error(f"[{model.upper()}] fetch reply error: {e}")
            return Reply(ReplyType.ERROR, f"[{model.upper()}] {e}")

    def _build_request_meta(self, query, session_id, model):
        prompt_ratio = self._parse_aspect_ratio_from_prompt(query)
        if prompt_ratio:
            logger.info(f"[{model.upper()}] 从 prompt 中解析到比例: {prompt_ratio}")

        quoted_cache = memory.USER_QUOTED_IMAGE_CACHE.get(session_id)
        if quoted_cache:
            images = self._build_edit_images(quoted_cache.get("files", []), model)
            memory.USER_QUOTED_IMAGE_CACHE.pop(session_id)
            if images:
                aspect_ratio = prompt_ratio or self._infer_aspect_ratio_from_images(quoted_cache.get("files", []))
                if not prompt_ratio:
                    logger.info(f"[{model.upper()}] 从回复引用图取参考图推断比例: {aspect_ratio}, count={len(images)}")
                return {
                    "mode": "edit",
                    "reference_count": len(images),
                    "aspect_ratio": aspect_ratio,
                    "size": self._build_size(model_state.get_image_size(session_id), aspect_ratio, model),
                    "quality": self._build_quality(model),
                    "images": images,
                }

        file_cache = memory.USER_IMAGE_CACHE.get(session_id)
        if file_cache:
            images = self._build_edit_images(file_cache.get("files", []), model)
            memory.USER_IMAGE_CACHE.pop(session_id)
            if images:
                aspect_ratio = prompt_ratio or self._infer_aspect_ratio_from_images(file_cache.get("files", []))
                if not prompt_ratio:
                    logger.info(f"[{model.upper()}] 从内存参考图推断比例: {aspect_ratio}, count={len(images)}")
                return {
                    "mode": "edit",
                    "reference_count": len(images),
                    "aspect_ratio": aspect_ratio,
                    "size": self._build_size(model_state.get_image_size(session_id), aspect_ratio, model),
                    "quality": self._build_quality(model),
                    "images": images,
                }

        aspect_ratio = prompt_ratio or conf().get("image_aspect_ratio", "16:9")
        return {
            "mode": "generate",
            "reference_count": 0,
            "aspect_ratio": aspect_ratio,
            "size": self._build_size(model_state.get_image_size(session_id), aspect_ratio, model),
            "quality": self._build_quality(model),
            "images": [],
        }

    def _create_or_edit_image(self, model, prompt, size, quality, images, image_count):
        kwargs = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "quality": quality,
            "n": image_count,
        }
        if images:
            kwargs["image"] = images if len(images) > 1 else images[0]
            return self._call_images_api(self.client.images.edit, kwargs, model, "edit")
        return self._call_images_api(self.client.images.generate, kwargs, model, "generate")

    def _call_images_api(self, api_method, kwargs, model, action):
        attempts = [
            dict(kwargs),
            dict(kwargs, response_format="b64_json"),
        ]
        last_err = None
        for attempt in attempts:
            try:
                return api_method(**attempt)
            except TypeError as e:
                last_err = e
                logger.warning(f"[{model.upper()}] {action} with args={list(attempt.keys())} failed: {e}")
                continue
            except openai.BadRequestError as e:
                if "response_format" not in attempt:
                    last_err = e
                    logger.warning(f"[{model.upper()}] {action} fallback with response_format=b64_json: {e}")
                    continue
                raise
        if last_err:
            raise last_err
        raise RuntimeError(f"{action} image failed")

    def _extract_images(self, response, model):
        image_results = []
        for item in getattr(response, "data", []) or []:
            if getattr(item, "b64_json", None):
                # Official SDK examples treat b64_json as raw base64, but some OpenAI-compatible gateways return a data URL here.
                mime_type, base64_data = self._normalize_base64_image_payload(item.b64_json)
                image_storage = io.BytesIO(base64.b64decode(base64_data))
                image_results.append({
                    "mime_type": mime_type,
                    "base64_data": base64_data,
                    "image_storage": image_storage,
                    "data_url": f"data:{mime_type};base64,{base64_data}",
                    "remote_url": None,
                })
                continue

            if getattr(item, "url", None):
                import requests

                resp = requests.get(item.url, timeout=60)
                resp.raise_for_status()
                mime_type = resp.headers.get("content-type", "image/png").split(";")[0]
                base64_data = base64.b64encode(resp.content).decode("utf-8")
                image_results.append({
                    "mime_type": mime_type,
                    "base64_data": base64_data,
                    "image_storage": io.BytesIO(resp.content),
                    "data_url": f"data:{mime_type};base64,{base64_data}",
                    "remote_url": item.url,
                })
                continue

            logger.warning(f"[{model.upper()}] unexpected image item: {item}")
        return image_results

    def _normalize_base64_image_payload(self, payload):
        normalized = str(payload or "").strip()
        mime_type = "image/png"
        if normalized.startswith("data:") and "base64," in normalized:
            header, normalized = normalized.split("base64,", 1)
            mime_type = header[5:].rstrip(";") or mime_type

        normalized = "".join(normalized.split())
        padding = len(normalized) % 4
        if padding:
            normalized = normalized + ("=" * (4 - padding))
        return mime_type, normalized

    def _build_edit_images(self, image_files, model):
        images = []
        for idx, image_file in enumerate(image_files):
            file_obj = self._encode_image_file(image_file, model, idx)
            if file_obj:
                images.append(file_obj)
        return images

    def _encode_image_file(self, image, model, idx):
        try:
            if image.mode in ("RGBA", "LA", "P"):
                fmt = "PNG"
                mime_type = "image/png"
            else:
                fmt = "JPEG"
                mime_type = "image/jpeg"
                if image.mode != "RGB":
                    image = image.convert("RGB")
            buf = io.BytesIO()
            image.save(buf, format=fmt)
            buf.seek(0)
            buf.name = f"reference_{idx}.{fmt.lower()}"
            buf.mime_type = mime_type
            return buf
        except Exception as e:
            logger.warning(f"[{model.upper()}] failed to encode cached image: {e}")
            return None

    def _infer_aspect_ratio_from_images(self, images):
        if not images:
            return conf().get("image_aspect_ratio", "16:9")
        max_width = 1
        max_height = 1
        for image in images:
            width, height = getattr(image, "size", (1, 1))
            if width * height > max_width * max_height:
                max_width, max_height = width, height
        return self._normalize_aspect_ratio(max_width, max_height)

    def _normalize_aspect_ratio(self, width, height):
        if not width or not height:
            return "1:1"
        ratio = width / height
        if ratio >= 1.4:
            return "16:9"
        if ratio <= 0.72:
            return "9:16"
        return "1:1"

    def _build_size(self, configured_size, aspect_ratio, model=None):
        normalized_size = str(configured_size or "").strip().lower()
        if normalized_size in self._SUPPORTED_SIZES:
            return normalized_size
        if self._supports_flexible_size(model) and self._is_valid_flexible_size(normalized_size):
            return normalized_size
        if normalized_size in {"256x256", "512x512", "1024x1024"}:
            return "1024x1024"
        if normalized_size in {"1k", "2k", "4k"}:
            if self._supports_flexible_size(model):
                return self._size_from_preset(normalized_size, aspect_ratio)
            return self._size_from_preset("1k", aspect_ratio)
        return self._size_from_preset("1k", aspect_ratio)

    def _size_from_preset(self, preset, aspect_ratio):
        preset_sizes = self._PRESET_SIZE_MAP.get(preset, self._PRESET_SIZE_MAP["1k"])
        orientation = self._resolve_orientation(aspect_ratio)
        return preset_sizes[orientation]

    def _resolve_orientation(self, aspect_ratio):
        ratio = str(aspect_ratio or "").strip().lower()
        if ratio in {"9:16", "3:4", "2:3", "1:2"}:
            return "portrait"
        if ratio in {"16:9", "4:3", "3:2", "2:1", "21:9"}:
            return "landscape"
        return "square"

    def _supports_flexible_size(self, model):
        return model in self._FLEXIBLE_SIZE_MODELS

    def _build_quality(self, model):
        configured_quality = str(conf().get("image_create_quality", "low") or "low").strip().lower()
        session_quality = None
        try:
            session_quality = model_state.get_image_quality(self.context_session_id)
        except Exception:
            session_quality = None
        if session_quality:
            configured_quality = str(session_quality).strip().lower()
        if configured_quality in self._SUPPORTED_QUALITIES:
            return configured_quality
        logger.warning(f"[{model.upper()}] invalid image_create_quality={configured_quality}, fallback to low")
        return "low"

    def _is_valid_flexible_size(self, size):
        match = re.fullmatch(r"(\d{2,4})x(\d{2,4})", str(size or "").strip().lower())
        if not match:
            return False

        width = int(match.group(1))
        height = int(match.group(2))
        if width > self._MAX_EDGE or height > self._MAX_EDGE:
            return False
        if width % 16 != 0 or height % 16 != 0:
            return False

        long_edge = max(width, height)
        short_edge = min(width, height)
        if short_edge <= 0:
            return False
        if (long_edge / short_edge) > self._MAX_ASPECT_RATIO:
            return False

        total_pixels = width * height
        if total_pixels < self._MIN_TOTAL_PIXELS or total_pixels > self._MAX_TOTAL_PIXELS:
            return False
        return True

    def _parse_aspect_ratio_from_prompt(self, prompt):
        return parse_aspect_ratio_from_prompt(prompt)

    def _parse_sequential_image_count_from_prompt(self, prompt):
        if not prompt:
            return None

        normalized_prompt = prompt.replace("张图", "张").replace("幅图", "幅")
        digit_patterns = [
            r"(?:组图|一组|做|生成|输出|画|给我|来|要)\s*(\d{1,2})\s*(?:张|幅)",
            r"(\d{1,2})\s*(?:张|幅)\s*(?:组图|系列图|连环图)",
            r"(?:共|一共|最多|至多|最多生成)\s*(\d{1,2})\s*(?:张|幅)",
        ]
        for pattern in digit_patterns:
            match = re.search(pattern, normalized_prompt, re.IGNORECASE)
            if match:
                return self._clamp_sequential_image_count(int(match.group(1)))

        cn_pattern = r"(两|俩|仨|[一二三四五六七八九十])\s*(?:张|幅)\s*(?:组图|系列图|连环图)?"
        match = re.search(cn_pattern, normalized_prompt)
        if match:
            count = self._SEQUENTIAL_IMAGE_CN_NUM_MAP.get(match.group(1))
            return self._clamp_sequential_image_count(count)
        return None

    def _clamp_sequential_image_count(self, count):
        if not count:
            return None
        return max(1, min(int(count), self._SEQUENTIAL_IMAGE_MAX_COUNT))

    def _normalize_openai_base_url(self, base_url):
        normalized = str(base_url or "https://api.openai.com/v1").rstrip("/")
        if not normalized.endswith("/v1"):
            normalized = f"{normalized}/v1"
        return normalized
