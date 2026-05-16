import base64

from google.genai.types import Part

from bot.bot import Bot
from bot.gemini.gemini_common import (
    clear_image_context_marker,
    extract_inline_image,
    get_gemini_image_settings_for_session,
    get_paid_client,
    get_user_image_chat,
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


_GEMINI_IMAGE_REFERENCE_MAX_COUNT = 14


class GoogleGeminiImageBot(Bot):
    def __init__(self):
        super().__init__()
        self.paid_client = get_paid_client(conf().get("gemini_api_key_paid"))
        self.safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]

    def reply(self, query, context: Context = None) -> Reply:
        try:
            session_id = context["session_id"]
            model = model_state.get_image_model(session_id)
            image_mode = model_state.get_image_mode(session_id)
            session_manager = get_chat_session_manager(session_id) or _gemini_sessions
            logger.info(f"[{model.upper()}] mode={image_mode}, query={query}, requester={session_id}")
            session_manager.session_query(query, session_id)

            request_contents, aspect_ratio = self._build_request_contents(query, session_id, model)
            user_image_chat = get_user_image_chat(
                session_id,
                model,
                paid_client=self.paid_client,
                safety_settings=self.safety_settings,
                aspect_ratio=aspect_ratio
            )
            response = user_image_chat.send_message(request_contents)

            try:
                mime_type, image_bytes = extract_inline_image(response)
                if image_bytes:
                    session_manager.session_inject_media(
                        session_id=session_id,
                        media_type="image",
                        data=base64.b64encode(image_bytes).decode("utf-8"),
                        source_model=model,
                        mime_type=mime_type
                    )
                    clear_image_context_marker(session_id)
                    logger.info(f"[{model.upper()}] image injected to session, model={model}, session_id={session_id}")
            except Exception as e:
                logger.warning(f"[{model.upper()}] failed to inject image to session: {e}")

            return Reply(ReplyType.IMAGE, response)
        except Exception as e:
            logger.error(f"[{model.upper()}] fetch reply error: {e}")
            return Reply(ReplyType.ERROR, f"[{model.upper()}] {e}")

    def _build_request_contents(self, query, session_id, model):
        text = Part.from_text(text=query)
        prompt_aspect_ratio = self._parse_aspect_ratio_from_prompt(query)
        if prompt_aspect_ratio:
            logger.info(f"[{model.upper()}] 从 prompt 中解析到比例: {prompt_aspect_ratio}")
        quoted_cache = memory.USER_QUOTED_IMAGE_CACHE.get(session_id)
        if quoted_cache:
            reference_images = self._limit_reference_images(quoted_cache["files"], model, "回复引用图")
            request_contents = list(reference_images)
            request_contents.append(text)
            aspect_ratio = prompt_aspect_ratio or infer_gemini_aspect_ratio_from_images(reference_images)
            memory.USER_QUOTED_IMAGE_CACHE.pop(session_id)
            if not prompt_aspect_ratio:
                logger.info(
                    f"[{model.upper()}] 从回复引用图取参考图推断比例: {aspect_ratio}, count={len(reference_images)}"
                )
            return request_contents, aspect_ratio

        file_cache = memory.USER_IMAGE_CACHE.get(session_id)
        if file_cache:
            reference_images = self._limit_reference_images(file_cache["files"], model, "内存参考图")
            request_contents = list(reference_images)
            request_contents.append(text)
            aspect_ratio = prompt_aspect_ratio or infer_gemini_aspect_ratio_from_images(reference_images)
            memory.USER_IMAGE_CACHE.pop(session_id)
            if not prompt_aspect_ratio:
                logger.info(
                    f"[{model.upper()}] 从内存参考图推断比例: {aspect_ratio}, count={len(reference_images)}"
                )
            return request_contents, aspect_ratio

        image_settings = get_gemini_image_settings_for_session(session_id, prompt_aspect_ratio)
        logger.info(
            f"[{model.upper()}] 当前为文生图模式, aspect_ratio={image_settings['aspect_ratio']}, image_size={image_settings['size']}"
        )
        return [text], prompt_aspect_ratio

    def _parse_aspect_ratio_from_prompt(self, prompt):
        return parse_aspect_ratio_from_prompt(prompt)

    def _limit_reference_images(self, images, model, source_name):
        reference_images = list(images or [])
        if len(reference_images) > _GEMINI_IMAGE_REFERENCE_MAX_COUNT:
            logger.info(
                f"[{model.upper()}] Gemini 图片参考图最多支持 {_GEMINI_IMAGE_REFERENCE_MAX_COUNT} 张，"
                f"{source_name}已从 {len(reference_images)} 张裁剪为 {_GEMINI_IMAGE_REFERENCE_MAX_COUNT} 张"
            )
        return reference_images[:_GEMINI_IMAGE_REFERENCE_MAX_COUNT]
