import time
import anthropic
import base64
import re
import docx
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
from PIL import Image


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
                system_prompt = file_prompt + self.system_prompt
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
            time.sleep(5)
            logger.warn(f"[CLAUDE] do retry, times={retry_count}")
            # Pop last role message avoiding the same two adjacent role messages during retrying.
            session.messages.pop()
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
        if path[-5:] == '.docx':
            reader = docx.Document(path)
            texts = ''.join([(para.text + '\n') for para in reader.paragraphs])
            number_of_pages = int(len(texts)/600) + 1
        elif path[-4:] == '.pdf':
            reader = PdfReader(path)
            number_of_pages = len(reader.pages)
            texts = ''.join([page.extract_text() for page in reader.pages])
        total_characters = len(texts)
        line_list = texts.splitlines()
        # 统计每一场的字数
        paragraph = ""
        sc_count = 0
        counter_dict ={}
        # 定义场次描述规则 
        pattern = r"第.*场|场景.*"
        for i, v in enumerate(line_list):
            # 只要每行的前3～7个字，符合场次描述规则
            if any(re.match(pattern, v[:n]) is not None for n in (3, 4, 5, 6, 7)):
                counter_dict[f"第{sc_count}场"] = f'{len(paragraph)}字'
                sc_count += 1
                paragraph = ""
            paragraph += v.strip()
        # 循环结束后，捕获最后一场戏的字数
        counter_dict[f"第{sc_count}场"] = f'{len(paragraph)}字'
        del counter_dict["第0场"]
        file_prompt = f'''\
        Here are some information for you to reference for your task:\n
        <ScreenPlay>\n
        <Number_of_Pages>
        {number_of_pages}
        </Number_of_Pages>\n
        <Total_Characters>
        {total_characters}
        </Total_Characters>\n
        <Scene_Characters>
        {counter_dict}
        </Scene_Characters>\n
        <Detail_Conent>
        {texts}
        </Detail_Conent>\n
        </ScreenPlay>
        Please just response with the exact result,that means excluding any friendly preamble before providing the requested output.
        '''
        return file_prompt

    def claude_vision(self, query, context):
        session_id = context.kwargs["session_id"]
        msg = context.kwargs["msg"]
        img_path = context.content
        msg.prepare()
        logger.info(f"[CLAUDI] query with images, path={img_path}")
        img = Image.open(img_path)
        # check if the image has an alpha channel
        if img.mode in ('RGBA','LA') or (img.mode == 'P' and 'transparency' in img.info):
            # Convert the image to RGB mode,whick removes the alpha channel
            img = img.convert('RGB')
            # Save the converted image
            img_path_no_alpha = img_path[:len(img_path)-3] + 'jpg'
            img.save(img_path_no_alpha)
            # Update img_path with the path to the converted image
            img_path = img_path_no_alpha

        with open(img_path, "rb") as image_file:
            binary_data = image_file.read()
            base_64_encoded_data = base64.b64encode(binary_data)
            base64_string = base_64_encoded_data.decode('utf-8')
        image_query = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64_string}}
        self.sessions.session_query(image_query, session_id)
        #return Reply(ReplyType.TEXT, f"[CLAUDI] query with images, path={img_path}")