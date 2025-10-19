"""
Bytedance volcengine_ark bot

@author zhayujie
@Date 2025/10/19
"""

import os

from volcenginesdkarkruntime import Ark

from config import conf
from bot.bot import Bot
from bot.session_manager import SessionManager
from bot.baidu.baidu_wenxin_session import BaiduWenxinSession
from bridge.context import ContextType, Context
from bridge.reply import Reply, ReplyType
from common.log import logger
from common.tool_button import tool_state

class VolcengineArkBot(Bot):

    def __init__(self):
        super().__init__()
        self.api_key = conf().get("ark_api_key")
        self.model = conf().get('model')
        self.Model_ID = self.model.upper()
        self.thinking = conf().get('thinking')
        self.image_model = conf().get('text_to_image')
        self.IMAGE_MODEL_ID = self.image_model.upper()
        
        self.sessions = SessionManager(BaiduWenxinSession, model=self.model or "gpt-3.5-turbo") # 复用文心的token计算方式

        self.client = Ark(
            api_key=self.api_key,
            # The base URL for model invocation .
            base_url="https://ark.cn-beijing.volces.com/api/v3",
        )

    def reply(self, query, context: Context = None) -> Reply:
        try:
            if context.type == ContextType.TEXT:
                session_id = context["session_id"]
                # 使用用户特定的状态
                is_imaging = tool_state.get_image_state(session_id)
                Model_ID = self.IMAGE_MODEL_ID if is_imaging else self.Model_ID
                logger.info(f"[{Model_ID}] query={query}, requester={session_id}")
                session = self.sessions.session_query(query, session_id)
                messages = session.messages
                completion = self.client.chat.completions.create(
                    # Get Model ID: https://www.volcengine.com/docs/82379/1330310 .
                    model=self.model,
                    messages=messages,
                    thinking = {
                        "type": self.thinking
                    }
                )
                reply_text = completion.choices[0].message.content
                total_tokens = completion.usage.total_tokens
                self.sessions.session_reply(reply_text, session_id, total_tokens) 
                return Reply(ReplyType.TEXT, reply_text)
        except Exception as e:
            logger.error("[{}] fetch reply error, {}".format(self.Model_ID, e))
            return Reply(ReplyType.ERROR, f"[{self.Model_ID}] {e}")