import time
import anthropic
from bot.bot import Bot
from bot.claude.claude_ai_session import ClaudeAiSession
from bot.chatgpt.chat_gpt_session import ChatGPTSession
from bot.openai.open_ai_image import OpenAIImage
from bot.session_manager import SessionManager
from bridge.context import Context, ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from config import conf


class ClaudeAIBot(Bot, OpenAIImage):
    def __init__(self):
        super().__init__()
        self.client = anthropic.Anthropic(api_key=conf().get("claude_api_key"))
        self.model = conf().get("model")
        self.sessions = SessionManager(ClaudeAiSession, model=conf().get("model") or "gpt-3.5-turbo")
        self.system_prompt = conf().get("character_desc")
        self.claude_api_cookie = conf().get("claude_api_cookie")
        self.proxy = conf().get("proxy")
        
    def reply(self, query, context: Context = None) -> Reply:
        if context.type == ContextType.TEXT:
            return self._chat(query, context)
        elif context.type == ContextType.IMAGE_CREATE:
            ok, res = self.create_img(query, 0)
            if ok:
                reply = Reply(ReplyType.IMAGE_URL, res)
            else:
                reply = Reply(ReplyType.ERROR, res)
            return reply
        else:
            reply = Reply(ReplyType.ERROR, "Bot不支持处理{}类型的消息".format(context.type))
            return reply
        
    def _chat(self, query, context, retry_count=0) -> Reply:
        """
        发起对话请求
        :param query: 请求提示词
        :param context: 对话上下文
        :param retry_count: 当前递归重试次数
        :return: 回复
        """
        if retry_count >= 2:
            # exit from retry 2 times
            logger.warn("[CLAUDEAI] failed after maximum number of retry times")
            return Reply(ReplyType.ERROR, "请再问我一次吧")

        try:
            session_id = context["session_id"]
            """if self.org_uuid is None:
                return Reply(ReplyType.ERROR, self.error)"""

            session = self.sessions.session_query(query, session_id)
            query_lists = session.messages
            """con_uuid = self.conversation_share_check(session_id)"""

           # model = conf().get("model") or "gpt-3.5-turbo"
           # remove system message
            """if session.messages[0].get("role") == "system":
                if model == "wenxin" or model == "claude":
                    session.messages.pop(0)"""
            logger.info(f"[CLAUDEAI] query={query}")

            response = self.client.messages.create(
                model=self.model,
                max_tokens=1000,
                temperature=0.0,
                system=self.system_prompt,
                messages=query_lists
            )
            reply_content = response.content[0].text
            logger.info(f"[CLAUDE] reply={reply_content}, total_tokens=invisible")
            self.sessions.session_reply(reply_content, session_id, 100)
            return Reply(ReplyType.TEXT, reply_content)

        except Exception as e:
            logger.exception(e)
            # retry
            time.sleep(2)
            logger.warn(f"[CLAUDE] do retry, times={retry_count}")
            return self._chat(query, context, retry_count + 1)
