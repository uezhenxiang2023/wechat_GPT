import base64

from google.genai.types import Part

from bot.bot import Bot
from bot.gemini.gemini_common import (
    clear_image_context_marker,
    data_url_to_part,
    extract_inline_image,
    get_image_context_from_session,
    get_paid_client,
    get_user_image_chat,
)
from bot.gemini.google_gemini_session import _gemini_sessions
from bridge.context import Context
from bridge.reply import Reply, ReplyType
from common import memory
from common.log import logger
from common.model_status import model_state
from config import conf


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
            logger.info(f"[{model.upper()}] query={query}, requester={session_id}")
            _gemini_sessions.session_query(query, session_id)

            user_image_chat = get_user_image_chat(
                session_id,
                model,
                paid_client=self.paid_client,
                safety_settings=self.safety_settings
            )
            request_contents = self._build_request_contents(query, session_id)
            response = user_image_chat.send_message(request_contents)

            try:
                mime_type, image_bytes = extract_inline_image(response)
                if image_bytes:
                    _gemini_sessions.session_inject_media(
                        session_id=session_id,
                        media_type="image",
                        data=base64.b64encode(image_bytes).decode("utf-8"),
                        source_model=model,
                        mime_type=mime_type
                    )
                    clear_image_context_marker(session_id)
                    logger.info(f"[GoogleGeminiImage] image injected to session, model={model}, session_id={session_id}")
            except Exception as e:
                logger.warning(f"[GoogleGeminiImage] failed to inject image to session: {e}")

            return Reply(ReplyType.IMAGE, response)
        except Exception as e:
            logger.error(f"[GoogleGeminiImage] fetch reply error: {e}")
            return Reply(ReplyType.ERROR, f"[GoogleGeminiImage] {e}")

    def _build_request_contents(self, query, session_id):
        text = Part.from_text(text=query)
        file_cache = memory.USER_IMAGE_CACHE.get(session_id)
        if file_cache:
            request_contents = list(file_cache["files"])
            request_contents.append(text)
            memory.USER_IMAGE_CACHE.pop(session_id)
            return request_contents

        image_context = get_image_context_from_session(session_id)
        session_images = image_context["images"]
        if session_images:
            request_contents = [data_url_to_part(image_url) for image_url in session_images]
            request_contents.append(text)
            logger.info(f"[GoogleGeminiImage] 从 session 历史取参考图, count={len(session_images)}")
            return request_contents

        return [text]
