import base64

from bot.bot import Bot
from bot.gemini.gemini_common import (
    GeminiVideoGenerationError,
    data_url_to_pil_image,
    generate_video,
    get_image_from_session,
    get_paid_client,
    infer_gemini_aspect_ratio_from_data_urls,
    infer_gemini_aspect_ratio_from_images,
)
from bot.gemini.google_gemini_session import _gemini_sessions
from bridge.context import Context
from bridge.reply import Reply, ReplyType
from common.aspect_ratio import parse_aspect_ratio_from_prompt
from common import memory
from common.log import logger
from common.model_status import model_state
from common.utils import get_chat_session_manager
from config import conf


class GoogleGeminiVideoBot(Bot):
    def __init__(self):
        super().__init__()
        self.paid_client = get_paid_client(conf().get("gemini_api_key_paid"))

    def reply(self, query, context: Context = None) -> Reply:
        try:
            session_id = context["session_id"]
            model = model_state.get_video_state(session_id)
            session_manager = get_chat_session_manager(session_id) or _gemini_sessions
            logger.info(f"[{model.upper()}] query={query}, requester={session_id}")
            session_manager.session_query(query, session_id)

            image, last_image, ref_images, aspect_ratio = self._get_video_inputs(query, session_id)
            response = generate_video(
                paid_client=self.paid_client,
                session_id=session_id,
                video_model=model,
                prompt=query,
                image=image,
                last_image=last_image,
                ref_images=ref_images,
                aspect_ratio=aspect_ratio
            )

            try:
                session_manager.session_inject_media(
                    session_id=session_id,
                    media_type="video",
                    data=base64.b64encode(response.video_bytes).decode("utf-8"),
                    source_model=model,
                    mime_type="video/mp4"
                )
                logger.info(f"[GoogleGeminiVideo] video injected to session, model={model}, session_id={session_id}")
            except Exception as e:
                logger.warning(f"[GoogleGeminiVideo] failed to inject video to session: {e}")

            return Reply(ReplyType.VIDEO, response)
        except GeminiVideoGenerationError as e:
            logger.error(f"[GoogleGeminiVideo] business error: {e}")
            return Reply(ReplyType.ERROR, str(e))
        except Exception as e:
            logger.error(f"[GoogleGeminiVideo] fetch reply error: {e}")
            return Reply(ReplyType.ERROR, "Gemini 视频生成失败，请稍后重试。")

    def _get_video_inputs(self, query, session_id):
        prompt_aspect_ratio = self._parse_aspect_ratio_from_prompt(query)
        if prompt_aspect_ratio:
            logger.info(f"[GoogleGeminiVideo] 从 prompt 中解析到比例: {prompt_aspect_ratio}")
        file_cache = memory.USER_QUOTED_IMAGE_CACHE.get(session_id)
        images = []
        aspect_ratio = prompt_aspect_ratio
        if file_cache:
            images = list(file_cache["files"])
            memory.USER_QUOTED_IMAGE_CACHE.pop(session_id)
            aspect_ratio = prompt_aspect_ratio or self._normalize_video_aspect_ratio(
                infer_gemini_aspect_ratio_from_images(images)
            )
            logger.info(f"[GoogleGeminiVideo] 从回复引用图取参考图, count={len(images)}")
        else:
            file_cache = memory.USER_IMAGE_CACHE.get(session_id)
            if file_cache:
                images = list(file_cache["files"])
                memory.USER_IMAGE_CACHE.pop(session_id)
                aspect_ratio = prompt_aspect_ratio or self._normalize_video_aspect_ratio(
                    infer_gemini_aspect_ratio_from_images(images)
                )
                logger.info(f"[GoogleGeminiVideo] 从内存参考图取参考图, count={len(images)}")
            else:
                session_images = get_image_from_session(session_id)
                if session_images:
                    images = [data_url_to_pil_image(image_url) for image_url in session_images]
                    aspect_ratio = prompt_aspect_ratio or self._normalize_video_aspect_ratio(
                        infer_gemini_aspect_ratio_from_data_urls(session_images)
                    )
                    logger.info(f"[GoogleGeminiVideo] 从 session 历史取参考图, count={len(images)}")

        if len(images) == 1:
            return images[0], None, None, aspect_ratio
        if len(images) == 2:
            return images[0], images[1], None, aspect_ratio
        if len(images) >= 3:
            return None, None, images, aspect_ratio or self._normalize_video_aspect_ratio(None)
        return None, None, None, aspect_ratio or self._normalize_video_aspect_ratio(None)

    def _parse_aspect_ratio_from_prompt(self, prompt):
        ratio_map = {
            16 / 9: "16:9",
            9 / 16: "9:16",
        }
        return parse_aspect_ratio_from_prompt(
            prompt,
            ratio_map=ratio_map,
            decimal_tolerance=1.0,
            ratio_tolerance=1.0
        )

    def _normalize_video_aspect_ratio(self, aspect_ratio):
        if aspect_ratio in {"16:9", "9:16"}:
            return aspect_ratio
        if aspect_ratio:
            ratio_value = self._ratio_value(aspect_ratio)
            if ratio_value is not None:
                return "16:9" if ratio_value >= 1 else "9:16"
        configured = str(conf().get("image_aspect_ratio", "16:9")).strip()
        if configured in {"16:9", "9:16"}:
            return configured
        ratio_value = self._ratio_value(configured)
        if ratio_value is not None:
            return "16:9" if ratio_value >= 1 else "9:16"
        return "16:9"

    def _ratio_value(self, aspect_ratio):
        try:
            width, height = str(aspect_ratio).split(":", 1)
            return float(width) / float(height)
        except (TypeError, ValueError, ZeroDivisionError):
            return None
