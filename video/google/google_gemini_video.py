import base64

from bot.bot import Bot
from bot.gemini.gemini_common import (
    GeminiVideoGenerationError,
    data_url_to_pil_image,
    generate_video,
    get_gemini_video_settings,
    get_paid_client,
    infer_gemini_aspect_ratio_from_data_urls,
    infer_gemini_aspect_ratio_from_images,
)
from bot.gemini.google_gemini_session import _gemini_sessions
from bridge.context import Context
from bridge.reply import Reply, ReplyType
from common.aspect_ratio import parse_aspect_ratio_from_prompt
from common import const, memory
from common.log import logger
from common.model_status import model_state
from common.utils import get_chat_session_manager, get_image_urls_from_session
from common.video_status import video_state
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

            image, last_image, ref_images, aspect_ratio, reference_image_count = self._get_video_inputs(
                query,
                session_id,
                model,
                session_manager
            )
            video_settings = get_gemini_video_settings(
                model,
                resolution=video_state.get_video_resolution(session_id),
                duration=video_state.get_video_duration(session_id),
                has_reference_images=reference_image_count > 0 and ref_images is not None,
            )
            resolution = video_settings["resolution"]
            duration_seconds = video_settings["duration_seconds"]
            logger.info(f"[{model.upper()}] 参考素材统计: reference_images={reference_image_count}")
            logger.info(
                f"[{model.upper()}] 请求参数: resolution={resolution}, "
                f"ratio={aspect_ratio}, duration={duration_seconds}"
            )
            logger.info(f"[{model.upper()}] polling task status via SDK")
            response = generate_video(
                paid_client=self.paid_client,
                session_id=session_id,
                video_model=model,
                prompt=query,
                image=image,
                last_image=last_image,
                ref_images=ref_images,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                duration_seconds=duration_seconds
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

    def _get_video_inputs(self, query, session_id, model, session_manager):
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
            session_images = get_image_urls_from_session(session_id, session_manager)
            if session_images:
                images = [data_url_to_pil_image(image_url) for image_url in session_images]
                aspect_ratio = prompt_aspect_ratio or self._normalize_video_aspect_ratio(
                    infer_gemini_aspect_ratio_from_data_urls(session_images)
                )
                logger.info(f"[GoogleGeminiVideo] 从 session 历史取参考图, count={len(images)}")
            else:
                file_cache = memory.USER_IMAGE_CACHE.get(session_id)
                if file_cache:
                    images = list(file_cache["files"])
                    memory.USER_IMAGE_CACHE.pop(session_id)
                    aspect_ratio = prompt_aspect_ratio or self._normalize_video_aspect_ratio(
                        infer_gemini_aspect_ratio_from_images(images)
                    )
                    logger.info(f"[GoogleGeminiVideo] 从内存参考图取参考图, count={len(images)}")

        if images:
            normalized_ratio = aspect_ratio or self._normalize_video_aspect_ratio(None)
            if self._supports_reference_images(model):
                reference_images = images[:3]
                if len(images) > 3:
                    logger.warning(
                        f"[GoogleGeminiVideo] reference images exceed limit, "
                        f"truncated from {len(images)} to {len(reference_images)}"
                    )
                logger.info(
                    f"[GoogleGeminiVideo] 使用参考图模式, count={len(reference_images)}, model={model}"
                )
                return None, None, reference_images, normalized_ratio, len(reference_images)
            if len(images) == 1:
                logger.info(f"[GoogleGeminiVideo] 当前模型不支持 reference_images，降级为首帧模式")
                return images[0], None, None, normalized_ratio, 1
            if len(images) == 2:
                logger.info(f"[GoogleGeminiVideo] 当前模型不支持 reference_images，降级为首尾帧模式")
                return images[0], images[1], None, normalized_ratio, 2
            raise GeminiVideoGenerationError(
                "Gemini 当前只有 Veo 3.1 和 Veo 3.1 Fast 支持多参考图生成视频。"
                "请切换到对应模型，或将参考图数量减少到 2 张及以内后再试。"
            )
        return None, None, None, aspect_ratio or self._normalize_video_aspect_ratio(None), 0

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

    def _supports_reference_images(self, model):
        return model in {const.VEO_31, const.VEO_31_FAST}
