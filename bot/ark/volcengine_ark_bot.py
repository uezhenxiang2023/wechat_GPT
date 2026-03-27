"""
Bytedance volcengine_ark bot

@author fort
@Date 2025/10/19
"""

from volcenginesdkarkruntime import Ark

from config import conf
from bot.bot import Bot
from bot.ark.ark_media import process_image_files
from bot.session_manager import SessionManager
from bot.ark.volcengine_ark_session import VolcengineArkSession
from bridge.context import ContextType, Context
from bridge.reply import Reply, ReplyType
from common import const, memory
from common.log import logger
from common.model_status import model_state

_ark_sessions = SessionManager(VolcengineArkSession, model=const.DOUBAO_SEED_20)

class VolcengineArkBot(Bot):

    def __init__(self):
        super().__init__()
        self.api_key = conf().get("ark_api_key")
        self.system_prompt = conf().get("character_desc") 
        self.sessions = _ark_sessions

        self.client = Ark(
            api_key=self.api_key
        )

    def reply(self, query, context: Context = None) -> Reply:
        try:
            session_id = context["session_id"]
            self.model = model_state.get_basic_state(session_id)
            self.Model_ID = self.model.upper()

            if context.type != ContextType.TEXT:
                return Reply(ReplyType.ERROR, "Only text context is supported")

            logger.info(f"[{self.Model_ID}] query={query}, requester={session_id}")

            # 检查缓存中是否媒体文件
            file_cache = memory.USER_IMAGE_CACHE.get(session_id)

            if file_cache:
                image_contents = process_image_files(file_cache)
                image_contents.append({"type": "text", "text": query})
                query = image_contents
                memory.USER_IMAGE_CACHE.pop(session_id)

            session = self.sessions.session_query(query, session_id)
            client_attr = 'bot_chat' if self.model in const.DOUBAO_BOT_LIST else 'chat'
            # 如果调用bot,去掉messages列表中的system prompt
            if client_attr == 'bot_chat' and session.messages[0].get('role') == 'system':
                session.messages.pop(0)
            completion = getattr(self.client, client_attr).completions.create(
                model=self.model,
                messages=session.messages,
                #thinking={"type": self.thinking}
            )
            reply_text = completion.choices[0].message.content
            total_tokens = completion.usage.total_tokens if client_attr == 'chat' else completion.bot_usage.model_usage[0].total_tokens
            logger.info(f"[{self.Model_ID}] reply={reply_text}, requester={session_id}")
            self.sessions.session_reply(reply_text, session_id, total_tokens)
            return Reply(ReplyType.TEXT, reply_text)

        except Exception as e:
            logger.error(f"[{self.Model_ID}] fetch reply error, {e}")
            return Reply(ReplyType.ERROR, f"[{self.Model_ID}] {e}")
