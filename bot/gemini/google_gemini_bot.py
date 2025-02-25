"""
Google gemini bot

@author zhayujie
@Date 2023/12/15
"""
# encoding:utf-8

import base64
import json
import os
import time
import re
import docx
from pypdf import PdfReader
from bot.bot import Bot
import google.generativeai as generativeai
from google.ai.generativelanguage_v1beta.types import content
from google import genai
from google.genai.types import Tool,GenerateContentConfig,GoogleSearch,Part,FunctionDeclaration
from bot.session_manager import SessionManager
from bridge.context import ContextType, Context
from bridge.reply import Reply, ReplyType
from common.log import logger
from common import const, memory
from config import conf
from bot.baidu.baidu_wenxin_session import BaiduWenxinSession
from bot.gemini.google_genimi_vision import GeminiVision
from PIL import Image
from channel.telegram.telegram_channel import escape


# OpenAI对话模型API (可用)
class GoogleGeminiBot(Bot,GeminiVision):

    def __init__(self):
        super().__init__()
        self.api_key = conf().get("gemini_api_key")
        self.model = conf().get('model')
        self.Model_ID = self.model.upper()
        self.system_prompt = conf().get("character_desc")
        self.function_call_dicts = {
            "screenplay_scenes_breakdown": self.screenplay_scenes_breakdown
        }
        # 复用文心的token计算方式
        self.sessions = SessionManager(BaiduWenxinSession, model=self.model or "gpt-3.5-turbo")
        
        # Initialize a client according to old generativeai SDK
        generativeai.configure(api_key=self.api_key,transport='rest')
        self.generation_config = {
            "temperature": 0.4,
            "top_p": 1,
            "top_k": 1,
            "max_output_tokens": 2048,
        }
        self.safety_settings = [
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
        self.tool_config={'function_calling_config': 'AUTO'}

        self.generative_model = generativeai.GenerativeModel(
                        model_name=self.model,
                        generation_config=self.generation_config,
                        safety_settings=self.safety_settings,
                        system_instruction=self.system_prompt,
                        tools = [
                            generativeai.protos.Tool(
                                function_declarations = [
                                    generativeai.protos.FunctionDeclaration(
                                        name = "screenplay_scenes_breakdown",
                                        description = "第一步先拆解剧本场景，提取场号、场景、内外、日夜等基础信息，形成场景列表；第二步根据输入的剧本名称找到剧本文件，准确统计剧本总页数，总字数，每场戏的字数，补充到场景列表中。",
                                        parameters = content.Schema(
                                            type = content.Type.OBJECT,
                                            enum = [],
                                            required = ["screenplay_title","scenes_list"],
                                            properties = {
                                                "screenplay_title": content.Schema(
                                                    type = content.Type.STRING,
                                                    description = "剧本名称",
                                                ),
                                                "scenes_list": content.Schema(
                                                    type = content.Type.ARRAY,
                                                    items = content.Schema(
                                                        type = content.Type.OBJECT,
                                                        properties = {
                                                            "id": content.Schema(
                                                                type = content.Type.INTEGER,
                                                                description = "场号",
                                                            ),
                                                            "location": content.Schema(
                                                                type = content.Type.STRING,
                                                                description = "场景名称",
                                                            ),
                                                            "daynight": content.Schema(
                                                                type = content.Type.STRING,
                                                                description = "日景还是夜景",
                                                                enum = ["日","夜"]
                                                            ),
                                                            "envirement": content.Schema(
                                                                type = content.Type.STRING,
                                                                description = "室内环境还是室外环境",
                                                                enum = ["内","外"]
                                                            ),
                                                        },
                                                    ),
                                                ),
                                            },
                                        ),
                                    ),
                                ],
                            ),
                        ],
                        tool_config=self.tool_config
                    )

        # Initialize a client according to new genai SDK
        self.client = genai.Client(api_key=self.api_key)

         # schema for screenplay_scenes_breakdown need to be updated
        """self.screenplay_scenes_breakdown_schema = FunctionDeclaration(
            name="screenplay_scenes_breakdown",
            description="第一步先拆解剧本场景，提取场号、场景、内外、日夜等基础信息，形成场景列表；第二步根据输入的剧本名称找到剧本文件，准确统计剧本总页数，总字数，每场戏的字数，补充到场景列表中。",
            parameters=content.Schema(
                type=content.Type.OBJECT,
                enum=[],
                required=["screenplay_title"],
                properties={
                    "screenplay_title": content.Schema(
                        type=content.Type.STRING,
                        description="剧本名称",
                    ),
                    "scenes_list": content.Schema(
                        type=content.Type.ARRAY,
                        items=content.Schema(
                            type=content.Type.OBJECT,
                            properties={
                                "id": content.Schema(
                                    type=content.Type.INTEGER,
                                    description="场号",
                                ),
                                "location": content.Schema(
                                    type=content.Type.STRING,
                                    description="场景名称",
                                ),
                                "daynight": content.Schema(
                                    type=content.Type.STRING,
                                    description="日景还是夜景",
                                ),
                                "envirement": content.Schema(
                                    type=content.Type.STRING,
                                    description="室内环境还是室外环境",
                                ),
                            },
                        ),
                    ),
                },
            ),
        )"""
        #self.function_declarations = Tool(function_declarations=[self.screenplay_scenes_breakdown_schema])
        self.google_search_tool = Tool(google_search=GoogleSearch())
        self.chat = self.client.chats.create(
            model=self.model,
            config=GenerateContentConfig(
                system_instruction=self.system_prompt,
                safety_settings=self.safety_settings,
                tools=[self.google_search_tool],
                response_modalities=['TEXT'],
                **self.generation_config
            )
        )

    def reply(self, query, context: Context = None) -> Reply:
        try:
            if context.type == ContextType.FILE:
                mime_type = context.content[(context.content.rindex('.') + 1):]
                if mime_type in const.AUDIO or mime_type in const.VIDEO or mime_type == const.PDF:
                    session_id = context["session_id"]
                    session = self.sessions.session_query(query, session_id)
                    return self.gemini_15_media(query, context, session)
                elif mime_type in ['docx','doc']:
                    self._file_cache(context)
                    doc_cache = memory.USER_FILE_CACHE.get(context['session_id'])
                    return self._file_download(doc_cache)
            elif context.type == ContextType.IMAGE:
                if self.model in const.GEMINI_15_FLASH_LIST or self.model in const.GEMINI_15_PRO_LIST or self.model in const.GEMINI_2_FLASH_LIST:
                    session_id = context["session_id"]
                    session = self.sessions.session_query(query, session_id)
                    return self.gemini_15_media(query, context, session)
                elif self.model in const.GEMINI_1_PRO_LIST:
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
                elif self.model in const.GEMINI_15_PRO_LIST or self.model in const.GEMINI_15_FLASH_LIST or self.model in const.GEMINI_2_FLASH_LIST:
                    gemini_messages = self._convert_to_gemini_15_messages(session.messages)

                if self.model in const.GEMINI_GENAI_SDK:
                    # 检查缓存中是否媒体文件
                    file_cache = memory.USER_IMAGE_CACHE.get(session_id)
                    if file_cache:
                        first_data = file_cache['files'][0]
                        data_type = type(first_data).__name__
                        if data_type == 'dict':
                            file_content = [Part.from_bytes(**first_data)]
                            file_content.append(query)
                            query = file_content
                        elif data_type == 'JpegImageFile':
                            file_cache['files'].append(query)
                            query = file_cache['files']
                        memory.USER_IMAGE_CACHE.pop(session_id)  
                    response = self.chat.send_message(query)

                elif self.model not in const.GEMINI_GENAI_SDK:
                    chat_session = self.generative_model.start_chat(
                        # 消息队战中的最新数据抛出去，留给send_message方法，从query中拿
                        history=gemini_messages[:-1],
                        enable_automatic_function_calling=True
                    )
                    response = chat_session.send_message(query)

                    # check function_call status
                    for part in response.parts:
                        if fn := part.function_call:
                            fn_dict = type(fn).to_dict(fn)
                            fn_name = fn_dict.get('name')
                            fn_args = fn_dict.get('args')
                            function_call_reply = {
                                "functionCall": {
                                    "name": fn_name,
                                    "args": fn_args
                                }
                            }
                            # 将reply_text转换为字符串
                            function_call_str = json.dumps(function_call_reply)
                            # add function call to session as model/assistant message
                            self.sessions.session_reply(function_call_str, session_id)

                            # call function
                            function_call = self.function_call_dicts.get(fn_name)
                            # 从fn_args中获取function_call的参数
                            api_response = function_call(**fn_args)
                            fn_args.update(**api_response)
                            function_response = {
                                "functionResponse": {
                                    "name": fn_name,
                                    "response": {
                                        "name": fn_name,
                                        "content": fn_args
                                    }
                                }
                            }
                            # 将function_response转换为字符串
                            function_response_str = json.dumps(function_response)
                            # add function response to session as user message
                            self.sessions.session_query(function_response_str, session_id)

                            # new turn of model request with function response
                            gemini_messages = self._convert_to_gemini_15_messages(session.messages)
                            chat_session = self.generative_model.start_chat(
                            # 消息队战中的最新数据抛出去，留给send_message方法，从query中拿
                                history=gemini_messages,
                                enable_automatic_function_calling=True
                            )
                            response = chat_session.send_message(query)

                raw_reply_text = self.get_reply_text(response)
                reply_text = escape(raw_reply_text)

                # attach search sources from the Grounded response
                if response.candidates[0].grounding_metadata.grounding_chunks:
                    inline_url = self.get_search_sources(response)
                    reply_text = f'{reply_text}\n\n{inline_url}'

                self.sessions.session_reply(reply_text, session_id)
                logger.info(f"[{self.Model_ID}] reply={reply_text}")
                return Reply(ReplyType.TEXT, reply_text)
            else:
                logger.warning(f"[{self.Model_ID}] Unsupported message type, type={context.type}")
                return Reply(ReplyType.ERROR, f"[{self.Model_ID}] Unsupported message type, type={context.type}")
        except Exception as e:
            logger.error("[{}] fetch reply error, {}".format(self.Model_ID, e))
            return Reply(ReplyType.ERROR, f"[{self.Model_ID}] {e}")

    def get_reply_text(self, response):
        parts = response.candidates[0].content.parts
        text = ''
        if parts is None:
            finish_reason = response.candidates[0].finish_reason
            print(f'{finish_reason=}')
            return
        for part in response.candidates[0].content.parts:
            if part.text:
                text += part.text
            elif part.executable_code:
                continue
        return text
    
    def get_search_sources(self, response):
        """
        Get search sources from the response Grounded with Google Search
        """
        sources = []
        ground_chunks = response.candidates[0].grounding_metadata.grounding_chunks
        for i, ground_chunk in enumerate(ground_chunks):
            title = ground_chunk.web.title
            title = escape(title)
            uri = ground_chunk.web.uri
            source = f'{i+1}\.[{title}]({uri})'
            sources.append(source)
        inline_url = '\n'.join(sources)
        return inline_url

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
                if msg_type in ['str','JpegImageFile','dict']:
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
    
    def _file_cache(self, context):
        memory.USER_FILE_CACHE[context['session_id']] = {
            "path": context.content,
            "msg": context.get("msg")
        }
        logger.info("[{}] file={} is cached for assistant".format(self.Model_ID, context.content))
        return None
    
    def _file_download(self, file_cache):
        msg = file_cache.get("msg")
        path = file_cache.get("path")
        msg.prepare()
        logger.info("[{}] file={} is downloaded locally".format(self.Model_ID, path))
        return None

    def screenplay_scenes_breakdown(self, *, screenplay_title: str = None, scenes_list: list = []):
        # 遍历./tmp目录下的文件,如果文件名中含有screenplay_title,则将其路径赋值给path
        for root, dirs, files in os.walk('./tmp'):
            for file in files:
                if screenplay_title in file:
                    path = os.path.join(root, file)

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
        total_words = len(texts)
        # 统计每一场的字数
        paragraph = ""
        sc_count = 0
        counter_dict ={}
        # 定义场次描述规则 
        pattern = r"第.*场|场景.*|\d+\..*"
        for i, v in enumerate(line_list):
            # 只要每行的前3～7个字，符合场次描述规则
            if any(re.match(pattern, v[:n]) is not None for n in (2, 3, 4, 5, 6, 7)):
                counter_dict[f"scene{sc_count}"] = f'{len(paragraph)}'
                sc_count += 1
                paragraph = ""
            paragraph += v.strip()
        # 循环结束后，捕获最后一场戏的字数
        counter_dict[f"scene{sc_count}"] = f'{len(paragraph)}'
        del counter_dict["scene0"]

        for i, v in enumerate(scenes_list):
            v.update(word_count = counter_dict[f"scene{i+1}"])

        api_response = {
            'total_pages':number_of_pages,
            'total_words':total_words,
            'scenes_list':scenes_list
        }
        return api_response
    
    def gemini_15_media(self, query, context, session: BaiduWenxinSession):
        session_id = context.kwargs["session_id"]
        msg = context.kwargs["msg"]
        media_path = context.content
        logger.info(f"[{self.model}] query with media, path={media_path}")
        type_position = media_path.rfind('.') + 1
        mime_type = media_path[type_position:]
        if mime_type in const.IMAGE:
            type_id = 'image'
            # Image URL request
            if query[:8] == 'https://':
                media_file = media_path

            # Image base64 encoded request
            else:
                msg.prepare()
                img = Image.open(media_path)
                media_file = img
                # check if the image has an alpha channel
                if img.mode in ('RGBA','LA') or (img.mode == 'P' and 'transparency' in img.info):
                    # Convert the image to RGB mode,whick removes the alpha channel
                    img = img.convert('RGB')
                    # Save the converted image
                    img_path_no_alpha = media_path[:len(media_path)-3] + 'jpg'
                    img.save(img_path_no_alpha)
                    # Update img_path with the path to the converted image
                    media_file = img_path_no_alpha
        elif mime_type in const.AUDIO:
            msg.prepare()
            type_id = 'audio'
        elif mime_type in const.VIDEO:
            msg.prepare()
            type_id = 'video'
        elif mime_type == const.PDF:
            msg.prepare()
            # Read and b64enbode the PDF file smaller than 20MB
            with open(media_path, 'rb') as file:
                pdf_data = file.read()
                pdf_b64 = base64.b64encode(pdf_data).decode('utf-8')
            type_id = 'application'
            
            media_file = {
                'mime_type': f'{type_id}/{mime_type}',
                'data': pdf_b64
            }
        elif mime_type in const.DOCUMENT:
            msg.prepare()
            type_id = 'text'
        elif mime_type in const.SPREADSHEET:
            msg.prepare()
            type_id = 'text'
        elif mime_type in const.PRESENTATION:
            msg.prepare()
            type_id = 'application'
            mime_type = 'vnd.google-apps.presentation'
        # Clear original media file in user content avoiding duplicated commitment
        session.messages.pop()
        if (mime_type not in const.IMAGE) and (mime_type != const.PDF):
            media_file = self.upload_to_gemini(media_path, mime_type=f'{type_id}/{mime_type}')
        self.sessions.session_query(media_file, session_id)
        self.cache_media(media_path, media_file, context)
    
    def cache_media(self, media_path, media_file, context):
        session_id = context["session_id"]
        if session_id not in memory.USER_IMAGE_CACHE:
            memory.USER_IMAGE_CACHE[session_id] = {
                "path": [media_path],
                "files": [media_file]
            }
        else:
            memory.USER_IMAGE_CACHE[session_id]["path"].append(media_path)
            memory.USER_IMAGE_CACHE[session_id]["files"].append(media_file)
        logger.info(f"{media_path} cached to memory")
        return None

    def upload_to_gemini(self, path, mime_type=None):
        """Uploads the given file to Gemini.

        https://ai.google.dev/gemini-api/docs/prompting_with_media
        """
        file = generativeai.upload_file(path, mime_type=mime_type)
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
        file = generativeai.get_file(name)
        while file.state.name == "PROCESSING":
            print(".", end="", flush=True)
            time.sleep(10)
            file = generativeai.get_file(name)
        if file.state.name != "ACTIVE":
            raise Exception(f"File {file.name} failed to process")
        print("...all files ready")
        print()
         