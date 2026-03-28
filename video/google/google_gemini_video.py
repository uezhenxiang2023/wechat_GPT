import base64

from bot.bot import Bot
from bot.gemini.gemini_common import (
    GeminiVideoGenerationError,
    data_url_to_pil_image,
    generate_video,
    get_image_from_session,
    get_paid_client,
)
from bot.gemini.google_gemini_session import _gemini_sessions
from bridge.context import Context
from bridge.reply import Reply, ReplyType
from common import memory
from common.log import logger
from common.model_status import model_state
from config import conf


class GoogleGeminiVideoBot(Bot):
    def __init__(self):
        super().__init__()
        self.paid_client = get_paid_client(conf().get("gemini_api_key_paid"))

    def reply(self, query, context: Context = None) -> Reply:
        try:
            session_id = context["session_id"]
            model = model_state.get_video_state(session_id)
            logger.info(f"[{model.upper()}] query={query}, requester={session_id}")
            _gemini_sessions.session_query(query, session_id)

            image, last_image, ref_images = self._get_video_inputs(session_id)
            response = generate_video(
                paid_client=self.paid_client,
                session_id=session_id,
                video_model=model,
                prompt=query,
                image=image,
                last_image=last_image,
                ref_images=ref_images
            )

            try:
                _gemini_sessions.session_inject_media(
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

    def _get_video_inputs(self, session_id):
        file_cache = memory.USER_IMAGE_CACHE.get(session_id)
        images = []
        if file_cache:
            images = list(file_cache["files"])
            memory.USER_IMAGE_CACHE.pop(session_id)
        else:
            session_images = get_image_from_session(session_id)
            if session_images:
                images = [data_url_to_pil_image(image_url) for image_url in session_images]
                logger.info(f"[GoogleGeminiVideo] 从 session 历史取参考图, count={len(images)}")

        if len(images) == 1:
            return images[0], None, None
        if len(images) == 2:
            return images[0], images[1], None
        if len(images) >= 3:
            return None, None, images
        return None, None, None
