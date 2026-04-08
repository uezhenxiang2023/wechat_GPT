import base64
import io

from PIL import Image
from xai_sdk import Client

from common.aspect_ratio import parse_aspect_ratio_from_prompt
from bot.bot import Bot
from bridge.context import Context
from bridge.reply import Reply, ReplyType
from common import const, memory
from common.log import logger
from common.model_status import model_state
from common.utils import get_chat_session_manager
from config import conf


_GROK_IMAGE_RATIO_MAP = {
    "1:1": 1.0,
    "3:4": 3 / 4,
    "4:3": 4 / 3,
    "9:16": 9 / 16,
    "16:9": 16 / 9,
    "2:3": 2 / 3,
    "3:2": 3 / 2,
    "9:19.5": 9 / 19.5,
    "19.5:9": 19.5 / 9,
    "9:20": 9 / 20,
    "20:9": 20 / 9,
    "1:2": 1 / 2,
    "2:1": 2 / 1,
}
_GROK_IMAGE_MAX_BYTES = 1024 * 1024 * 2


class GrokImageBot(Bot):
    def __init__(self):
        super().__init__()
        self.client = Client(
            api_key=conf().get("grok_api_key"),
            timeout=conf().get("request_timeout", 180)
        )

    def reply(self, query, context: Context = None) -> Reply:
        try:
            session_id = context["session_id"]
            model = model_state.get_image_model(session_id)
            session_manager = get_chat_session_manager(session_id)
            logger.info(f"[{model.upper()}] query={query}, requester={session_id}")
            session_manager.session_query(query, session_id)

            image_args, model = self._build_image_args(query, session_id, model)
            image_size = self._normalize_resolution(conf().get("image_create_size", "1k"), model)
            response = self.client.image.sample(
                prompt=query,
                model=model,
                image_format="base64",
                resolution=image_size,
                **image_args
            )
            mime_type, base64_data = self._split_response_base64(response.base64)
            image_storage = io.BytesIO(base64.b64decode(base64_data))

            try:
                session_manager.session_inject_media(
                    session_id=session_id,
                    media_type="image",
                    data=base64_data,
                    source_model=model,
                    mime_type=mime_type
                )
                logger.info(f"[{model.upper()}] image injected to session, model={model}, session_id={session_id}")
            except Exception as e:
                logger.warning(f"[{model.upper()}] failed to inject image to session: {e}")

            return Reply(ReplyType.IMAGE, image_storage)
        except Exception as e:
            logger.error(f"[{model.upper()}] fetch reply error: {e}")
            return Reply(ReplyType.ERROR, f"[{model.upper()}] {e}")

    def _build_image_args(self, query, session_id, model):
        prompt_ratio = self._parse_aspect_ratio_from_prompt(query, model)
        if prompt_ratio:
            logger.info(f"[{model.upper()}] 从 prompt 中解析到比例: {prompt_ratio}")

        file_cache = memory.USER_QUOTED_IMAGE_CACHE.get(session_id)
        if file_cache:
            image_urls = [
                self._encode_pil_image(image_file, model)
                for image_file in file_cache.get("files", [])
            ]
            image_urls = [image_url for image_url in image_urls if image_url]
            memory.USER_QUOTED_IMAGE_CACHE.pop(session_id)
            if image_urls:
                image_urls = [self._compress_data_url(image_url, model) for image_url in image_urls]
                aspect_ratio = prompt_ratio or self._infer_aspect_ratio_from_data_urls(image_urls, model)
                if not prompt_ratio:
                    logger.info(f"[{model.upper()}] 从回复引用图取参考图推断比例: {aspect_ratio}, count={len(image_urls)}")
                return self._build_edit_args(image_urls, aspect_ratio, model)

        file_cache = memory.USER_IMAGE_CACHE.get(session_id)
        if file_cache:
            image_urls = [
                self._encode_pil_image(image_file, model)
                for image_file in file_cache.get("files", [])
            ]
            image_urls = [image_url for image_url in image_urls if image_url]
            memory.USER_IMAGE_CACHE.pop(session_id)
            if image_urls:
                image_urls = [self._compress_data_url(image_url, model) for image_url in image_urls]
                aspect_ratio = prompt_ratio or self._infer_aspect_ratio_from_data_urls(image_urls, model)
                if not prompt_ratio:
                    logger.info(f"[{model.upper()}] 从内存参考图推断比例: {aspect_ratio}, count={len(image_urls)}")
                return self._build_edit_args(image_urls, aspect_ratio, model)

        aspect_ratio = prompt_ratio or self._normalize_aspect_ratio(conf().get("image_aspect_ratio", "16:9"), model)
        image_size = self._normalize_resolution(conf().get("image_create_size", "1k"), model)
        logger.info(
            f"[{model.upper()}] 当前为文生图模式, aspect_ratio={aspect_ratio}, image_size={image_size}"
        )
        return {
            "aspect_ratio": aspect_ratio
        }, model

    def _build_edit_args(self, image_urls, aspect_ratio, model):
        if len(image_urls) == 1:
            return {
                "image_url": image_urls[0],
                "aspect_ratio": aspect_ratio
            }, model
        if model == const.GROK_IMAGINE_IMAGE_PRO:
            logger.info(
                f"[{model.upper()}] pro 暂时不支持多图编辑，自动降级到 {const.GROK_IMAGINE_IMAGE}"
            )
            model = const.GROK_IMAGINE_IMAGE
        return {
            "image_urls": image_urls[:5],
            "aspect_ratio": aspect_ratio
        }, model

    def _split_response_base64(self, encoded):
        if not encoded:
            raise ValueError("Empty base64 image response from Grok.")
        if encoded.startswith("data:") and "base64," in encoded:
            header, b64_data = encoded.split("base64,", 1)
            mime_type = header[5:].rstrip(";")
            return mime_type or "image/jpeg", b64_data
        return "image/jpeg", encoded

    def _encode_pil_image(self, image, model):
        try:
            if image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGB")
            fmt = "PNG" if image.format == "PNG" else "JPEG"
            mime_type = "image/png" if fmt == "PNG" else "image/jpeg"
            buf = io.BytesIO()
            image.save(buf, format=fmt)
            data = base64.b64encode(buf.getvalue()).decode("utf-8")
            return f"data:{mime_type};base64,{data}"
        except Exception as e:
            logger.warning(f"[{model.upper()}] failed to encode cached image: {e}")
            return None

    def _compress_data_url(self, image_url, model):
        try:
            if len(image_url.encode("utf-8")) <= _GROK_IMAGE_MAX_BYTES:
                return image_url

            header, b64_data = image_url.split(",", 1)
            image = Image.open(io.BytesIO(base64.b64decode(b64_data)))
            if image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGB")

            quality = 90
            width, height = image.size
            resized = image
            while True:
                buf = io.BytesIO()
                resized.save(buf, format="JPEG", quality=quality, optimize=True)
                encoded = base64.b64encode(buf.getvalue()).decode("utf-8")
                compressed_url = f"data:image/jpeg;base64,{encoded}"
                if len(compressed_url.encode("utf-8")) <= _GROK_IMAGE_MAX_BYTES:
                    logger.info(f"[{model.upper()}] 参考图已压缩，size={len(compressed_url.encode('utf-8'))} bytes")
                    return compressed_url
                if quality > 55:
                    quality -= 10
                    continue
                width = max(int(width * 0.85), 512)
                height = max(int(height * 0.85), 512)
                if (width, height) == resized.size:
                    logger.warning("[{model.upper()}] 参考图压缩后仍偏大，继续使用当前结果")
                    return compressed_url
                resized = image.resize((width, height), Image.LANCZOS)
                quality = 85
        except Exception as e:
            logger.warning(f"[{model.upper()}] failed to compress image for edit: {e}")
            return image_url

    def _normalize_resolution(self, resolution, model):
        normalized = str(resolution).strip().lower()
        if normalized in {"1k", "2k"}:
            return normalized
        logger.warning(f"[{model.upper()}] invalid resolution={resolution}, fallback to 1k")
        return "1k"

    def _normalize_aspect_ratio(self, aspect_ratio, model):
        normalized = str(aspect_ratio).strip()
        if normalized in _GROK_IMAGE_RATIO_MAP:
            return normalized
        logger.warning(f"[{model.upper()}] invalid aspect_ratio={aspect_ratio}, fallback to 16:9")
        return "16:9"

    def _parse_aspect_ratio_from_prompt(self, prompt, model):
        ratio_candidates = {value: key for key, value in _GROK_IMAGE_RATIO_MAP.items()}
        aspect_ratio = parse_aspect_ratio_from_prompt(
            prompt,
            ratio_map=ratio_candidates,
            decimal_tolerance=0.25,
            ratio_tolerance=0.3
        )
        return self._normalize_aspect_ratio(aspect_ratio, model) if aspect_ratio else None

    def _infer_aspect_ratio_from_data_urls(self, image_urls, model):
        sizes = []
        for image_url in image_urls:
            try:
                _, b64_data = image_url.split(",", 1)
                image = io.BytesIO(base64.b64decode(b64_data))
                with Image.open(image) as pil_image:
                    sizes.append(pil_image.size)
            except Exception as e:
                logger.warning(f"[{model.upper()}] failed to infer aspect ratio from image: {e}")
        if not sizes:
            return self._normalize_aspect_ratio(conf().get("image_aspect_ratio", "16:9"), model)
        best_size = sorted(sizes, key=lambda size: size[0] * size[1], reverse=True)[0]
        ratio = round(best_size[0] / best_size[1], 4)
        return min(_GROK_IMAGE_RATIO_MAP, key=lambda key: abs(_GROK_IMAGE_RATIO_MAP[key] - ratio))
