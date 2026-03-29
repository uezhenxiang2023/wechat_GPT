import base64
import io
import mimetypes

from bot.bot import Bot
from bot.grok.grok_session import GrokSession
from bot.session_manager import SessionManager
from bridge.context import Context, ContextType
from bridge.reply import Reply, ReplyType
from common import memory
from common.log import logger
from common.model_status import model_state
from config import conf
from xai_sdk import Client
from xai_sdk.chat import file as xai_file
from xai_sdk.chat import image as xai_image
from xai_sdk.chat import user


_grok_sessions = SessionManager(GrokSession, model="grok-4.20-0309-non-reasoning")


class GrokBot(Bot):
    def __init__(self):
        super().__init__()
        self.api_key = conf().get("grok_api_key")
        self.system_prompt = conf().get("character_desc")
        self.stream = conf().get("stream")
        self.request_timeout = conf().get("request_timeout", 180)
        self.sessions = _grok_sessions
        self.client = Client(api_key=self.api_key, timeout=self.request_timeout)

    def reply(self, query, context: Context = None) -> Reply:
        if context.type == ContextType.TEXT:
            return self._chat(query, context)
        if context.type in (ContextType.IMAGE, ContextType.FILE):
            return self._chat(query, context)
        return Reply(ReplyType.ERROR, "Bot不支持处理{}类型的消息".format(context.type))

    def _chat(self, query, context: Context) -> Reply:
        session_id = context["session_id"]
        self.model = model_state.get_basic_state(session_id)
        self.Model_ID = self.model.upper()

        try:
            current_content, uploaded_file_ids = self._build_current_content(query, session_id)
            logger.info(f"[{self.Model_ID}] query={query}")

            if self.stream:
                return Reply(ReplyType.STREAM, self._stream_reply(current_content, session_id, uploaded_file_ids))

            session = self.sessions.session_query(current_content, session_id)
            chat = self._create_chat(session, current_content)
            response = chat.sample()
            reply_text = response.content
            total_tokens = self._extract_total_tokens(response)
            session.previous_response_id = getattr(response, "id", None)
            session.remote_history_outdated = False
            logger.info(f"[{self.Model_ID}] reply={reply_text}, requester={session_id}")
            self.sessions.session_reply(reply_text, session_id, total_tokens)
            self._cleanup_uploaded_files(uploaded_file_ids)
            return Reply(ReplyType.TEXT, reply_text)
        except Exception as e:
            logger.error(f"[{self.Model_ID}] fetch reply error, {e}")
            return Reply(ReplyType.ERROR, f"[{self.Model_ID}] {e}")

    def _stream_reply(self, current_content, session_id, uploaded_file_ids):
        def generate():
            full_text = ""
            total_tokens = None
            try:
                session = self.sessions.session_query(current_content, session_id)
                chat = self._create_chat(session, current_content)
                response = None
                for response, chunk in chat.stream():
                    content = getattr(chunk, "content", "")
                    if content:
                        full_text += content
                        yield content

                if response is not None:
                    total_tokens = self._extract_total_tokens(response)
                    session.previous_response_id = getattr(response, "id", None)
                    session.remote_history_outdated = False

                self.sessions.session_reply(full_text, session_id, total_tokens)
                logger.info(f"[{self.Model_ID}] stream completed, requester={session_id}, tokens={total_tokens}")
            finally:
                self._cleanup_uploaded_files(uploaded_file_ids)

        return generate()

    def _build_current_content(self, query, session_id):
        current_content = []
        uploaded_file_ids = []
        file_cache = memory.USER_IMAGE_CACHE.get(session_id)

        if file_cache:
            for cached_file, file_path in zip(file_cache.get("files", []), file_cache.get("path", [])):
                data_type = type(cached_file).__name__

                if data_type in ("JpegImageFile", "PngImageFile", "Image"):
                    data_url = self._encode_pil_image(cached_file, data_type)
                    if data_url:
                        current_content.append(xai_image(data_url))
                elif data_type == "dict":
                    uploaded_file = self._upload_cached_file(cached_file, file_path)
                    if uploaded_file:
                        current_content.append(xai_file(uploaded_file.id))
                        uploaded_file_ids.append(uploaded_file.id)
                else:
                    logger.warning(f"[{self.Model_ID}] unsupported cached file type: {data_type}")

            memory.USER_IMAGE_CACHE.pop(session_id)

        current_content.insert(0, query)
        return current_content, uploaded_file_ids

    def _create_chat(self, session, current_content):
        if session.previous_response_id and not session.remote_history_outdated:
            chat = self.client.chat.create(
                model=self.model,
                previous_response_id=session.previous_response_id,
                store_messages=True,
            )
            chat.append(user(*current_content))
            return chat

        chat = self.client.chat.create(model=self.model, store_messages=True)
        for message in session.sdk_messages:
            chat.append(message)
        return chat

    def _encode_pil_image(self, image, data_type):
        try:
            media_type = "image/png" if data_type == "PngImageFile" else "image/jpeg"
            fmt = "PNG" if media_type == "image/png" else "JPEG"

            if fmt == "JPEG" and image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGB")

            buf = io.BytesIO()
            image.save(buf, format=fmt)
            data = base64.b64encode(buf.getvalue()).decode("utf-8")
            return f"data:{media_type};base64,{data}"
        except Exception as e:
            logger.error(f"[{self.Model_ID}] failed to encode image: {e}")
            return None

    def _upload_cached_file(self, cached_file, file_path):
        mime_type = cached_file.get("mime_type", "")
        raw_data = cached_file.get("data")
        filename = self._guess_filename(file_path, mime_type)

        try:
            file_bytes = self._decode_file_bytes(raw_data, mime_type)
            return self.client.files.upload(file_bytes, filename=filename)
        except Exception as e:
            logger.error(f"[{self.Model_ID}] failed to upload cached file: {e}")
            return None

    def _decode_file_bytes(self, raw_data, mime_type):
        if mime_type == "application/pdf":
            return base64.b64decode(raw_data)
        if isinstance(raw_data, str):
            return raw_data.encode("utf-8")
        return raw_data

    def _guess_filename(self, file_path, mime_type):
        if file_path:
            return file_path.split("/")[-1]
        extension = mimetypes.guess_extension(mime_type or "") or ".txt"
        return f"upload{extension}"

    def _cleanup_uploaded_files(self, file_ids):
        for file_id in file_ids:
            try:
                self.client.files.delete(file_id)
            except Exception as e:
                logger.warning(f"[{self.Model_ID}] failed to delete uploaded file {file_id}: {e}")

    def _extract_total_tokens(self, response):
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        return getattr(usage, "input_tokens", 0) + getattr(usage, "output_tokens", 0)
