"""
Google gemini bot

@author zhayujie
@Date 2023/12/15
"""
# encoding:utf-8

import re
import docx
from pypdf import PdfReader
from bot.bot import Bot
import google.generativeai as genai
from bot.session_manager import SessionManager
from bridge.context import ContextType, Context
from bridge.reply import Reply, ReplyType
from common.log import logger
from common import const, memory
from config import conf
from bot.baidu.baidu_wenxin_session import BaiduWenxinSession
from bot.gemini.google_genimi_vision import GeminiVision


# OpenAI对话模型API (可用)
class GoogleGeminiBot(Bot,GeminiVision):

    def __init__(self):
        super().__init__()
        self.api_key = conf().get("gemini_api_key")
        self.model = conf().get('model')
        self.system_prompt = conf().get("character_desc")
        # 复用文心的token计算方式
        self.sessions = SessionManager(BaiduWenxinSession, model=self.model or "gpt-3.5-turbo")

    def reply(self, query, context: Context = None) -> Reply:
        try:
            if context.type == ContextType.FILE:
                return self._file_cache(query, context)
            elif context.type == ContextType.TEXT:
                logger.info(f"[Gemini] query={query}")
                session_id = context["session_id"]
                session = self.sessions.session_query(query, session_id)
                gemini_messages = self._convert_to_gemini_messages(self._filter_messages(session.messages))

                # Set up the model
                if self.model in const.GEMINI_1_PRO_LIST:
                    system_prompt = None
                    vision_res = self.do_vision_completion_if_need(session_id,query) # Image recongnition and vision completion
                    if vision_res:
                        return vision_res
                elif self.model in const.GEMINI_15_PRO_LIST:
                    file_cache = memory.USER_FILE_CACHE.get(session_id)
                    if file_cache:
                        file_prompt = self.read_file(file_cache)
                        system_prompt = self.system_prompt + file_prompt
                    else:
                        system_prompt = self.system_prompt

                genai.configure(api_key=self.api_key,transport='rest')
                generation_config = {
                "temperature": 0.4,
                "top_p": 1,
                "top_k": 1,
                "max_output_tokens": 2048,
                }

                safety_settings = [
                {
                    "category": "HARM_CATEGORY_HARASSMENT",
                    "threshold": "BLOCK_NONE"
                },
                {
                    "category": "HARM_CATEGORY_HATE_SPEECH",
                    "threshold": "BLOCK_NONE"
                },
                {
                    "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "threshold": "BLOCK_NONE"
                },
                {
                    "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                    "threshold": "BLOCK_NONE"
                },
                ]
                model = genai.GenerativeModel(
                    model_name=self.model,
                    generation_config=generation_config,
                    safety_settings=safety_settings,
                    system_instruction=system_prompt
                )
                response = model.generate_content(gemini_messages)
                reply_text = response.text
                self.sessions.session_reply(reply_text, session_id)
                logger.info(f"[Gemini] reply={reply_text}")
                return Reply(ReplyType.TEXT, reply_text)
            else:
                logger.warn(f"[Gemini] Unsupported message type, type={context.type}")
                return Reply(ReplyType.ERROR, f"[Gemini] Unsupported message type, type={context.type}")
        except Exception as e:
            logger.error("[Gemini] fetch reply error, {}".format(e))

    def _convert_to_gemini_messages(self, messages: list):
        res = []
        for msg in messages:
            if msg.get("role") == "user":
                role = "user"
            elif msg.get("role") == "assistant":
                role = "model"
            else:
                continue
            res.append({
                "role": role,
                "parts": [{"text": msg.get("content")}]
            })
        return res

    def _filter_messages(self, messages: list):
        res = []
        turn = "user"
        for i in range(len(messages) - 1, -1, -1):
            message = messages[i]
            if message.get("role") != turn:
                continue
            res.insert(0, message)
            if turn == "user":
                turn = "assistant"
            elif turn == "assistant":
                turn = "user"
        return res
    
    def _file_cache(self, query, context):
        memory.USER_FILE_CACHE[context['session_id']] = {
            "path": context.content,
            "msg": context.get("msg")
        }
        logger.info("[GEMINI] file={} is assigned to assistant".format(context.content))
        return None
    
    def read_file(self,file_cache):
        msg = file_cache.get("msg")
        path = file_cache.get("path")
        msg.prepare()
        if path[-5:] == '.docx':
            reader = docx.Document(path)
            texts = ''
            line_list = []
            num_id = 1
            # 遍历文档中的段落
            for paragraph in reader.paragraphs:
                # 提取文本内容
                scene_normal = paragraph.text.strip()
                # 检查段落是否有序号
                if paragraph._p.pPr and paragraph._p.pPr.numPr:           
                    scene_normal = f"{num_id}. " + scene_normal
                    num_id += 1
                texts = texts + scene_normal + '\n'
                line_list.append(scene_normal)
            number_of_pages = len(texts)//500 + 1
        elif path[-4:] == '.pdf':
            reader = PdfReader(path)
            number_of_pages = len(reader.pages)
            texts = ''.join([page.extract_text() for page in reader.pages])
            line_list = texts.splitlines()
        total_characters = len(texts)
        # 统计每一场的字数
        paragraph = ""
        sc_count = 0
        counter_dict ={}
        # 定义场次描述规则 
        pattern = r"第.*场|场景.*|\d+\..*"
        for i, v in enumerate(line_list):
            # 只要每行的前3～7个字，符合场次描述规则
            if any(re.match(pattern, v[:n]) is not None for n in (2, 3, 4, 5, 6, 7)):
                counter_dict[f"第{sc_count}场"] = f'{len(paragraph)}字'
                sc_count += 1
                paragraph = ""
            paragraph += v.strip()
        # 循环结束后，捕获最后一场戏的字数
        counter_dict[f"第{sc_count}场"] = f'{len(paragraph)}字'
        del counter_dict["第0场"]
        file_prompt = f'''\
        \nHere are some information for you to reference for your task:\n
        Number_of_Pages:{number_of_pages}\n
        Total_Characters:{total_characters}\n
        Scene_Characters:{counter_dict}\n
        {texts}\n
        Please find quotes relevant to the question before answering.Just response with the exact result,that means excluding any friendly preamble before providing the requested output.
        '''
        return file_prompt
