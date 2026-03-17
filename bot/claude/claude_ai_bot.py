import io
import time
import base64

from anthropic import Anthropic
from bot.bot import Bot
from bot.claude.claude_ai_session import ClaudeAiSession
from bot.openai.open_ai_image import OpenAIImage
from bot.session_manager import SessionManager
from bridge.context import Context, ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from common import memory
from config import conf

from common.tool_button import tool_state
from common.model_status import model_state


class ClaudeAIBot(Bot, OpenAIImage):
    def __init__(self):
        super().__init__()
        self.client = Anthropic(
            api_key=conf().get("claude_api_key"),
            base_url=conf().get("claude_base_url")
        )
        self.sessions = SessionManager(ClaudeAiSession, model=conf().get("model") or "gpt-3.5-turbo")
        self.system_prompt = conf().get("character_desc")
        self.claude_api_cookie = conf().get("claude_api_cookie")
        self.stream = conf().get("stream")
        self.proxy = conf().get("proxy")

    def reply(self, query, context: Context = None) -> Reply:
        if context.type == ContextType.IMAGE:
            return self.claude_vision(query, context)
        elif context.type == ContextType.FILE:
            return self._file_cache(query, context)
        elif context.type == ContextType.TEXT:
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
        session_id = context["session_id"]
        self.model = model_state.get_basic_state(session_id)
        self.Model_ID = self.model.upper()
        if retry_count >= 2:
            # exit from retry 2 times
            logger.warning(f"[{self.Model_ID}] failed after maximum number of retry times")
            return Reply(ReplyType.ERROR, "请再问我一次吧")

        try:
            # 先构建多模态 content 块（含媒体+文本）
            current_content = self._build_current_content(query, session_id)
            logger.info(f"[{self.Model_ID}] query={query}")

            # 将多模态 content 块写入 session，而不是纯字符串 query
            #
            self.sessions.session_query(current_content, session_id)  # ← 传入列表，保留媒体块
            
            # 从 session 获取完整消息历史
            session = self.sessions.build_session(session_id)
            claude_message = session.messages

            # 判断联网搜索状态
            is_searching = tool_state.get_search_state(session_id)

            create_kwargs = dict(
                model=self.model,
                max_tokens=1000,
                temperature=0.0,
                system=self.system_prompt,
                messages=claude_message
            )
            
            # 联网搜索时走 IMAGE_URL 分支，把 response 整体传给 channel 处理
            if is_searching:
                create_kwargs["tools"] = [
                    {
                        "type": "web_search_20250305", 
                        "name": "web_search",
                        "max_uses": 5
                    }
                ]
                response = self.client.messages.create(**create_kwargs)

                # 提取文本回复
                reply_content = ""
                for block in response.content:
                    if hasattr(block, "text"):
                        reply_content += block.text

                logger.info(f"[{self.Model_ID}] reply={reply_content}, total_tokens=invisible")
                self.sessions.session_reply(reply_content, session_id, 100)
                return Reply(ReplyType.IMAGE_URL, response)
            
            elif self.stream:
                logger.info(f"[{self.Model_ID}] stream 模式已开启")
                
                def stream_generator():
                    full_text = ""
                    with self.client.messages.stream(**create_kwargs) as s:
                        for text_chunk in s.text_stream:
                            full_text += text_chunk
                            yield text_chunk
                        # 流结束，写回 session
                        final_message = s.get_final_message()
                        total_tokens = final_message.usage.output_tokens
                        self.sessions.session_reply(full_text, session_id, total_tokens)
                        logger.info(f"[{self.Model_ID}] stream 完成, session_id={session_id}, tokens={total_tokens}")

                return Reply(ReplyType.STREAM, stream_generator())

            else:
                # 普通模式
                response = self.client.messages.create(**create_kwargs)
                reply_content = ""
                for block in response.content:
                    if hasattr(block, "text"):
                        reply_content += block.text
                logger.info(f"[{self.Model_ID}] reply={reply_content}")
                self.sessions.session_reply(reply_content, session_id, 100)
                return Reply(ReplyType.TEXT, reply_content)

        except Exception as e:
            logger.error("[{}] fetch reply error, {}".format(self.Model_ID, e))
            return Reply(ReplyType.ERROR, f"[{self.Model_ID}] {e}")
        
    def _build_current_content(self, query: str, session_id: str) -> list:
        """
        构建当前轮次的多模态 content 块列表。
        媒体块在前，文本在后。消费并清除缓存。
        """
        current_content = []

        file_cache = memory.USER_IMAGE_CACHE.get(session_id)
        if file_cache:
            for cached_file in file_cache.get("files", []):
                data_type = type(cached_file).__name__

                if data_type in ("JpegImageFile", "PngImageFile", "Image"):
                    try:
                        import io
                        img = cached_file
                        if img.mode in ("RGBA", "LA", "P"):
                            img = img.convert("RGB")
                            fmt, media_type = "JPEG", "image/jpeg"
                        elif data_type == "PngImageFile":
                            fmt, media_type = "PNG", "image/png"
                        else:
                            fmt, media_type = "JPEG", "image/jpeg"

                        buf = io.BytesIO()
                        img.save(buf, format=fmt)
                        img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

                        current_content.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": img_b64
                            }
                        })
                        logger.debug(f"[{self.Model_ID}] image block added, format={fmt}")
                    except Exception as e:
                        logger.error(f"[{self.Model_ID}] failed to encode image: {e}")

                elif data_type == "dict":
                    mime_type = cached_file.get("mime_type", "")
                    raw_data = cached_file.get("data", "")

                    if mime_type == "application/pdf":
                        current_content.append({
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": raw_data
                            }
                        })
                        logger.debug(f"[{self.Model_ID}] PDF document block added")

                    elif mime_type in ("application/docx", "application/doc"):
                        current_content.append({
                            "type": "text",
                            "text": f"<document>\n{raw_data}\n</document>"
                        })
                        logger.debug(f"[{self.Model_ID}] DOCX text block added")

                    else:
                        logger.warning(f"[{self.Model_ID}] unsupported mime_type: {mime_type}")

                else:
                    logger.warning(f"[{self.Model_ID}] unsupported cached file type: {data_type}")

            memory.USER_IMAGE_CACHE.pop(session_id)

        # 文本块追加在末尾
        current_content.append({
            "type": "text",
            "text": query
        })

        return current_content