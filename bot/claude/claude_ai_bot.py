import time
import anthropic
import base64
from pypdf import PdfReader
from bot.bot import Bot
from bot.claude.claude_ai_session import ClaudeAiSession
from bot.openai.open_ai_image import OpenAIImage
from bot.session_manager import SessionManager
from bridge.context import Context, ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from common import memory
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
        if retry_count >= 2:
            # exit from retry 2 times
            logger.warn("[CLAUDEAI] failed after maximum number of retry times")
            return Reply(ReplyType.ERROR, "请再问我一次吧")

        try:
            file_cache = memory.USER_FILE_CACHE.get(session_id)
            if file_cache:
                file_prompt = self.read_file(file_cache)
                system_prompt = self.system_prompt + file_prompt
            else:
                system_prompt = self.system_prompt
            session = self.sessions.session_query(query, session_id)
            logger.info(f"[CLAUDEAI] query={query}")
            claude_message = self._convert_to_claude_messages(session.messages)

            response = self.client.messages.create(
                model=self.model,
                max_tokens=1000,
                temperature=0.0,
                system=system_prompt,
                messages=claude_message
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
        
    def _convert_to_claude_messages(self, messages: list):
        res = []
        image_content = []
        for item in messages:
            if item.get('role') == 'user':
                role = 'user'
                #如果user内容是图片,构建图片列表
                if type(item.get('content')).__name__ == 'dict':
                    image_content.append(item['content'])
                    continue
                elif type(item.get('content')).__name__ == 'str':
                    text_content = {'type': 'text', 'text': item['content']}
                #只要图片列表中有内容，就将文字请求补充进去
                if image_content != []:
                    image_content.append(text_content)
                    content = image_content.copy()
                else:
                    content = [text_content]
            elif item.get('role') == 'assistant':
                role = 'assistant'
                content = item['content']
                #只要assistant有回复，就将image_content清空，准备放入新一轮user内容中的图片
                image_content.clear()
            res.append({'role': role, 'content': content})
        return res
    
    def _file_cache(self, query, context):
        memory.USER_FILE_CACHE[context['session_id']] = {
            "path": context.content,
            "msg": context.get("msg")
        }
        logger.info("[CLAUDE] file={} is assigned to assistant".format(context.content))
        return None
    
    def read_file(self,file_cache):
        msg = file_cache.get("msg")
        path = file_cache.get("path")
        msg.prepare()
        reader = PdfReader(path)
        number_of_pages = len(reader.pages)
        texts = ''.join([page.extract_text() for page in reader.pages])
        total_characters = len(texts)
        file_prompt = f'''answer question according to the following information:\n
                Number_of_pages: {number_of_pages}\n
                Total_Characters: {total_characters}\n
                Detail_Conent: <paper>{texts}</paper>'''
        return file_prompt

    def claude_vision(self, query, context):
        session_id = context.kwargs["session_id"]
        msg = context.kwargs["msg"]
        img_path = context.content
        msg.prepare()
        logger.info(f"[CLAUDI] query with images, path={img_path}")

        with open(img_path, "rb") as image_file:
            binary_data = image_file.read()
            base_64_encoded_data = base64.b64encode(binary_data)
            base64_string = base_64_encoded_data.decode('utf-8')

        image_query = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64_string}}
        self.sessions.session_query(image_query, session_id)
        #return Reply(ReplyType.TEXT, f"[CLAUDI] query with images, path={img_path}")