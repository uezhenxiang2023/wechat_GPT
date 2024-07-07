# encoding:utf-8
import time, io

import openai

from openai import OpenAI
from bot.session_manager import SessionManager
from bot.chatgpt.chat_gpt_session import ChatGPTSession
from bot.chatgpt.chat_gpt_bot import ChatGPTBot
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from config import conf
from lib import itchat
from common.tmp_dir import TmpDir
from common import const, memory
from PIL import Image

client = OpenAI(api_key=conf().get("open_ai_api_key")) # Instantiate a client according to latest openai SDK

user_session = dict()


# OpenAI的Assistant对话模型API (可用)
class OpenAIAssistantBot(ChatGPTBot):
    def __init__(self):
        super().__init__()
        self.user_card_lists = []
        self.assistant_id = conf().get("OpenAI_Assistant_ID")
        self.vector_store_id = conf().get("OpenAI_Vector_Stores_ID")
        client.beta.assistants.update(
            assistant_id=self.assistant_id,
            tool_resources={
                'file_search':{
                    'vector_store_ids': [self.vector_store_id]
                }
            }
        )
        self.assistant = client.beta.assistants.retrieve(assistant_id=self.assistant_id)
        self.assistant_model = self.assistant.model
        self.sessions = SessionManager(ChatGPTSession, model=conf().get("model") or "gpt-3.5-turbo")

    def reply(self, query, context=None):            
        # acquire reply content
        if context.type == ContextType.TEXT:
            if self.assistant_model in const.GPT4_MULTIMODEL_LIST and query[:8] == 'https://':
                session = self.sessions.session_query(query, context["session_id"])
                return self.assistant_vision(query, context, session)
            # Image recongnition and vision completion with gpt-4-vision-preview
            if self.assistant_model == const.GPT4_VISION_PREVIEW:
                img_cache = memory.USER_IMAGE_CACHE.get(context.kwargs["session_id"])
                if img_cache:    
                    vision_res = self.do_vision_completion_if_need(context.kwargs["session_id"],query)
                    bot_type = "[GPT-4-VISION-PREVIEW]"
                    logger.info(f"{bot_type} query={query}")
                    content = vision_res
                else:
                    error_message = "OPENAI_ASSISTANT[{}]仅支持处理图片类型的消息，请先上传图片，或为OPENAI_ASSISTANT切换其他模型。"
                    logger.error(error_message.format(self.assistant_model.upper()))
                    reply = Reply(ReplyType.ERROR, error_message.format(self.assistant_model.upper()))
                    return reply
            else:
                bot_type = "OPENAI_ASSISTANT[{}]".format(self.assistant_model.upper())
                logger.info(f"{bot_type} query={query}")
                content = self.reply_text(query,context)
                if "image" in content and content["image"]:
                    reply_img = Reply(ReplyType.IMAGE, content["image"])
                    logger.info(f"{bot_type} reply={reply_img}")
                    receiver = context["receiver"]
                    image_storage = reply_img.content
                    image_storage.seek(0)
                    itchat.send_image(image_storage, toUserName=receiver)
                    logger.info("[WX] sendImage, receiver={}".format(receiver))
                    #return reply_img

            reply_content = content["content"]
            reply_txt = Reply(ReplyType.TEXT, reply_content)
            logger.info(f"{bot_type} reply={reply_txt}")
            return reply_txt        
        elif context.type == ContextType.IMAGE_CREATE:
            ok, retstring = self.create_img(query, 0)
            reply = None
            if ok:
                reply = Reply(ReplyType.IMAGE_URL, retstring)
            else:
                reply = Reply(ReplyType.ERROR, retstring)
            return reply      
        elif context.type == ContextType.FILE:
            return self._file_cache(context)
        elif context.type == ContextType.IMAGE:
            if self.assistant_model in const.GPT4_MULTIMODEL_LIST:
                session_id = context["session_id"]
                session = self.sessions.session_query(query, session_id)
                return self.assistant_vision(query, context, session)
            elif self.assistant_model == const.GPT4_VISION_PREVIEW:
                memory.USER_IMAGE_CACHE[context["session_id"]] = {
                    "path": context.content,
                    "msg": context.get("msg")
                }
                logger.info("Wait for [GPT-4-VISION-PREVIEW] query with images")
                return None
            else:
                logger.error("OPENAI_ASSISTANT[{}]不支持处理{}类型的消息".format(self.assistant_model.upper(), context.type))
                reply = Reply(ReplyType.ERROR, "OPENAI_ASSISTANT[{}]不支持处理{}类型的消息".format(self.assistant_model.upper(), context.type))
                return reply
        else:
            reply = Reply(ReplyType.ERROR, "OPENAI_ASSISTANT[{}]不支持处理{}类型的消息".format(self.assistant_model.upper(), context.type))
            return reply

    def reply_text(self, query, context, retry_count=0):
        try:
            result = {}
            response = self.run(query,context)
            result['image'], result['content'] = self.get_response(response[0], context) 
            return result
        
        except Exception as e:
            need_retry = retry_count < 2
            result = {"completion_tokens": 0, "content": "我现在有点累了，等会再来吧"}
            if isinstance(e, openai.RateLimitError):
                logger.warn("[OPEN_AI_ASSISTANT] RateLimitError: {}".format(e))
                result["content"] = "提问太快啦，请休息一下再问我吧"
                if need_retry:
                    time.sleep(20)
            elif isinstance(e, openai.Timeout):
                logger.warn("[OPEN_AI_ASSISTANT] Timeout: {}".format(e))
                result["content"] = "我没有收到你的消息"
                if need_retry:
                    time.sleep(5)
            elif isinstance(e, openai.APIConnectionError):
                logger.warn("[OPEN_AI_ASSISTANT] APIConnectionError: {}".format(e))
                need_retry = False
                result["content"] = "我连接不到你的网络"
            else:
                logger.warn("[OPEN_AI_ASSISTANT] Exception: {}".format(e))
                need_retry = False
                result["content"] = f"访问[OPEN_AI_ASSISTANT]时出错:{e}"

            if need_retry:
                logger.warn("[OPEN_AI_ASSISTANT] 第{}次重试".format(retry_count + 1))
                return self.reply_text(query, retry_count + 1)
            else:
                return result
    
    def _file_cache(self, context):
        memory.USER_FILE_CACHE[context['session_id']] = {
            "path": context.content,
            "msg": context.get("msg")
        }
        logger.info("file={} is cached".format(context.content))
        return None
    
    def assistant_file(self,context):
        path = context.content
        filename = path[len('tmp/'):]
        msg = context.kwargs['msg']
        file_list = client.files.list(purpose='assistants')
        vs_file_list = client.beta.vector_stores.files.list(vector_store_id=self.vector_store_id)
        file = None
        vs_file = None

        for item in file_list:
            if filename == item.filename:
                file = client.files.retrieve(file_id=item.id)
                break
        if file is None:
            msg.prepare()
            file = client.files.create(
                        file=open(path,'rb'),
                        purpose='assistants'
                    )
        for vs_item in vs_file_list:
            if file.id == vs_item.id:
                vs_file = vs_item.id
                break
        if vs_file is None:
            client.beta.vector_stores.files.create(
                vector_store_id=self.vector_store_id,
                file_id=file.id
            )
        logger.info("[OPENAI_ASSISTANT] file={} is assigned to assistant".format(context.content))
        return None
    
    def get_threadID(self,context):
        session_id = context.kwargs["session_id"]
        thread = {}
        for card_list in self.user_card_lists:
            if session_id == card_list['session_id']:
                thread = client.beta.threads.retrieve(card_list['thread_id'])
                break
        if thread:
            return thread
        else:
            thread = client.beta.threads.create()
            thread_id = thread.id
            new_card = {"session_id": session_id, "thread_id": thread_id}
            self.user_card_lists.append(new_card)
            return thread
    
    def wait_on_run(self,run,thread):
        while run.status == 'queued' or run.status == 'in_progress':
            run = client.beta.threads.runs.retrieve(
                thread_id=thread.id,
                run_id=run.id
            )
            time.sleep(0.5)
        return run

    def submit_message(self,assistant_id,thread,user_messages,session_id):
        file_cache = memory.USER_FILE_CACHE.get(session_id)
        file = None
        tools = [{'type': 'file_search'},{'type': 'code_interpreter'}]
        if file_cache:
            path = file_cache.get("path")
            file_name = path[len('tmp/'):]
            msg = file_cache.get("msg")
            type_position = path.index('.', -6) + 1
            mime_type = path[type_position:]
            tools = [tools[1]] if mime_type == 'xlsx' else tools
            file_list = client.files.list(purpose='assistants')
            for item in file_list:
                if file_name == item.filename:
                    file = client.files.retrieve(file_id=item.id)
                    break
            if file is None:
                msg.prepare()
                file = client.files.create(
                            file=open(path,'rb'),
                            purpose='assistants'
                        )
            memory.USER_FILE_CACHE[session_id] = None
        attachment = [{'file_id': file.id, 'tools': tools}] if file else []
        client.beta.threads.messages.create(
            thread_id=thread.id,
            role=user_messages.get('role'),
            content=user_messages.get('content'),
            attachments=attachment
        )
        return client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=assistant_id
        )
    
    def get_response(self, thread, context=None):
        image_storage = None
        thread_messages =client.beta.threads.messages.list(
            thread_id=thread.id,
            order='desc'
        )        
        assistant_messages = thread_messages.data[0].content
        for m in assistant_messages:
            if m.type == 'image_file':
                image_id = m.image_file.file_id
                image_data = client.files.content(image_id)
                image_data_bytes = image_data.read()
                image_storage = io.BytesIO(image_data_bytes)
                    
            elif m.type == 'text':
                assistant_message = m.text.value
                annotations_tpye = m.text.annotations[0].type if m.text.annotations else None
                if annotations_tpye == 'file_path':
                    file_id = m.text.annotations[0].file_path.file_id
                    file = client.files.content(file_id)
                    file_data = file.read() 
                    file_name = TmpDir().path() + m.text.annotations[0].text[18:]
                    with open(file_name, 'wb') as f:
                        f.write(file_data)
                    file_storage = file_name
                    itchat.send_file(file_storage, toUserName=context['receiver'])
                    logger.info("[WX] sendFile, receiver={}".format(context['receiver']))
        return image_storage, assistant_message 
    
    def run(self,query,context):
        thread = self.get_threadID(context)
        session_id = context["session_id"]
        session = self.sessions.session_query(query, session_id)
        user_messages = self._convert_to_assistant_messages(session.messages)
        self.sessions.clear_session(session_id)
        run = self.submit_message(self.assistant_id,thread,user_messages,session_id)
        run = self.wait_on_run(run,thread)
        return thread,run
    
    def assistant_vision(self, query, context, session: ChatGPTSession):
        session_id = context.kwargs["session_id"]
        msg = context.kwargs["msg"]
        img_path = context.content
        logger.info(f"OPENAI_ASSISTANT[{self.assistant_model}] query with images, path={img_path}")
        # Image URL request
        if query[:8] == 'https://':
            image_prompt = img_path
            image_query = {"type": 'image_url', 'image_url': {"url": image_prompt}}
            # Clear raw url in user content
            session.messages.pop()
        # Image base64 encoded request
        else:
            msg.prepare()
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
                file = client.files.create(
                    file=image_file,
                    purpose='vision'
                )
                image_prompt = file.id
            image_query = {"type": 'image_file', 'image_file': {"file_id": image_prompt}}
        self.sessions.session_query(image_query, session_id)

    def _convert_to_assistant_messages(self, messages: list):
        #res = []
        image_content = []
        text_content = {'type': 'text', 'text': messages[-1]['content']}
        for item in messages:
            if item.get('role') == 'user':
                #如果user内容是图片,构建图片列表
                if type(item.get('content')).__name__ == 'dict':
                    image_content.append(item['content'])
                    continue
                if type(item.get('content')).__name__ == 'str':
                    continue
            elif item.get('role') == 'assistant':
                continue
        #将图片识别的文字请求补充进去
        image_content.append(text_content)
        content = image_content.copy()
        res = {'role': 'user', 'content': content}
        return res
