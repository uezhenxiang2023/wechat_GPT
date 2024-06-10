"""
Google gemini bot

@author zhayujie
@Date 2023/12/15
"""
# encoding:utf-8

import time
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
from PIL import Image

genai.configure(api_key=conf().get('gemini_api_key'),transport='rest')

# OpenAI对话模型API (可用)
class GoogleGeminiBot(Bot,GeminiVision):

    def __init__(self):
        super().__init__()
        self.api_key = conf().get("gemini_api_key")
        self.model = conf().get('model')
        self.Model_ID = self.model.upper()
        self.system_prompt = conf().get("character_desc")
        # 复用文心的token计算方式
        self.sessions = SessionManager(BaiduWenxinSession, model=self.model or "gpt-3.5-turbo")

    def reply(self, query, context: Context = None) -> Reply:
        try:
            if context.type == ContextType.FILE:
                mime_type = context.content[(context.content.index('.') + 1):]
                if mime_type in const.AUDIO or mime_type in const.VIDEO or\
                   mime_type in const.SPREADSHEET or\
                   mime_type in const.PRESENTATION:
                    session_id = context["session_id"]
                    session = self.sessions.session_query(query, session_id)
                    return self.gemini_15_media(query, context, session)
                elif mime_type in const.DOCUMENT:
                    return self._file_cache(query, context)
            elif context.type == ContextType.IMAGE:
                if self.model in const.GEMINI_15_FLASH_LIST or self.model in const.GEMINI_15_PRO_LIST:
                    session_id = context["session_id"]
                    session = self.sessions.session_query(query, session_id)
                    return self.gemini_15_media(query, context, session)
                if self.model in const.GEMINI_1_PRO_LIST:
                    memory.USER_IMAGE_CACHE[context["session_id"]] = {
                    "path": context.content,
                    "msg": context.get("msg")
                    }
                    logger.info(f"{context.content} cached to memory")
                    return None
            elif context.type == ContextType.VIDEO:
                session_id = context["session_id"]
                session = self.sessions.session_query(query, session_id)
                return self.gemini_15_media(query, context, session)
            elif context.type == ContextType.TEXT:
                logger.info(f"[{self.Model_ID}] query={query}")
                session_id = context["session_id"]
                session = self.sessions.session_query(query, session_id)

                # Set up the model
                if self.model in const.GEMINI_1_PRO_LIST:
                    gemini_messages = self._convert_to_gemini_1_messages(self._filter_gemini_1_messages(session.messages))
                    system_prompt = None
                    vision_res = self.do_vision_completion_if_need(session_id,query) # Image recongnition and vision completion
                    if vision_res:
                        return vision_res
                elif self.model in const.GEMINI_15_PRO_LIST or self.model in const.GEMINI_15_FLASH_LIST:
                    gemini_messages = self._convert_to_gemini_15_messages(session.messages)
                    file_cache = memory.USER_FILE_CACHE.get(session_id)
                    if file_cache:
                        file_prompt = self.read_file(file_cache)
                        system_prompt = self.system_prompt + file_prompt
                    else:
                        system_prompt = self.system_prompt

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
                logger.info(f"[{self.Model_ID}] reply={reply_text}")
                return Reply(ReplyType.TEXT, reply_text)
            else:
                logger.warn(f"[{self.Model_ID}] Unsupported message type, type={context.type}")
                return Reply(ReplyType.ERROR, f"[{self.Model_ID}] Unsupported message type, type={context.type}")
        except Exception as e:
            logger.error("[{}] fetch reply error, {}".format(self.Model_ID, e))

    def _convert_to_gemini_1_messages(self, messages: list):
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

    def _convert_to_gemini_15_messages(self, messages: list):
        res = []
        media_parts = []
        user_parts = []
        assistant_parts = []
        for msg in messages:
            msg_role = msg.get('role')
            msg_content = msg.get('content')
            msg_type = type(msg_content).__name__  #识别消息内容的类型
            if msg.get("role") == "user":
                if msg_type == 'File':
                    media_parts.append(msg_content)
                    continue
                if msg_type == 'str':
                    if media_parts != []:
                        media_parts.append(msg_content)
                        user_parts = media_parts
                        parts = user_parts
                        media_parts = []
                    elif media_parts == []:
                        parts = [msg_content]
                role = "user"

            elif msg_role == "assistant":
                assistant_parts = [msg_content]
                parts = assistant_parts
                role = "model"
            else:
                continue
            res.append({
                "role": role,
                "parts": parts
            })
        return res

    def _filter_gemini_1_messages(self, messages: list):
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
        logger.info("[{}] file={} is cached for assistant".format(self.Model_ID, context.content))
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
    
    def gemini_15_media(self, query, context, session: BaiduWenxinSession):
        session_id = context.kwargs["session_id"]
        msg = context.kwargs["msg"]
        media_path = context.content
        logger.info(f"[{self.model}] query with media, path={media_path}")
        type_position = media_path.index('.') + 1
        mime_type = media_path[type_position:]
        if mime_type in const.IMAGE:
            type_id = 'image'
            # Image URL request
            if query[:8] == 'https://':
                image_prompt = media_path

            # Image base64 encoded request
            else:
                msg.prepare()
                img = Image.open(media_path)
                # check if the image has an alpha channel
                if img.mode in ('RGBA','LA') or (img.mode == 'P' and 'transparency' in img.info):
                    # Convert the image to RGB mode,whick removes the alpha channel
                    img = img.convert('RGB')
                    # Save the converted image
                    img_path_no_alpha = media_path[:len(media_path)-3] + 'jpg'
                    img.save(img_path_no_alpha)
                    # Update img_path with the path to the converted image
                    media_path = img_path_no_alpha
        elif mime_type in const.AUDIO:
            msg.prepare()
            type_id = 'audio'
        elif mime_type in const.VIDEO:
            msg.prepare()
            type_id = 'video'
        elif mime_type in const.SPREADSHEET:
            type_id = 'application'
            mime_type = 'vnd.google-apps.spreadsheet'
        elif mime_type in const.PRESENTATION:
            type_id = 'application'
            mime_type = 'vnd.google-apps.presentation'
        # Clear original media file in user content avoiding duplicated commitment
        session.messages.pop()
        media_file = self.upload_to_gemini(media_path, mime_type=f'{type_id}/{mime_type}')
        self.sessions.session_query(media_file, session_id)
    
    def upload_to_gemini(self, path, mime_type=None):
        """Uploads the given file to Gemini.

        https://ai.google.dev/gemini-api/docs/prompting_with_media
        """
        file = genai.upload_file(path, mime_type=mime_type)
        self.wait_for_files_active(file)
        print(f"Uploaded file '{file.display_name}' as: {file.uri}")
        return file
    
    def wait_for_files_active(self, media_file):
        """Waits for the given files to be active.

        Some files uploaded to the Gemini API need to be processed before they can be
        used as prompt inputs. The status can be seen by querying the file's "state"
        field.

        This implementation uses a simple blocking polling loop. Production code
        should probably employ a more sophisticated approach.
        """
        print("Waiting for file processing...")
        name =media_file.name
        file = genai.get_file(name)
        while file.state.name == "PROCESSING":
            print(".", end="", flush=True)
            time.sleep(10)
            file = genai.get_file(name)
        if file.state.name != "ACTIVE":
            raise Exception(f"File {file.name} failed to process")
        print("...all files ready")
        print()
         