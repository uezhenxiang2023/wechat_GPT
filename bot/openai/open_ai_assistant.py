# encoding:utf-8

import time

import openai

from openai import OpenAI
from bot.bot import Bot
from bot.openai.open_ai_image import OpenAIImage
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from config import conf

client = OpenAI() #Instantiate a client according to latest openai SDK

user_session = dict()


# OpenAI的Assistant对话模型API (可用)
class OpenAIAssistantBot(Bot, OpenAIImage):
    def __init__(self):
        super().__init__()
        client.api_key = conf().get("open_ai_api_key")
        self.assistant_id = conf().get("OpenAI_Assistant_ID")
        self.thread =client.beta.threads.create()

    def reply(self, query, context=None):            
        # acquire reply content
        if context and context.type:
            if context.type == ContextType.TEXT:
                logger.info("[OPENAI_ASSISTANT] query={}".format(query))
                content = self.reply_text(query)
                reply_content = content["content"]
                reply = Reply(ReplyType.TEXT, reply_content)
                logger.info(f"[OPENAI_ASSISTANT] reply={reply}")
                return reply
            
            elif context.type == ContextType.IMAGE_CREATE:
                ok, retstring = self.create_img(query, 0)
                reply = None
                if ok:
                    reply = Reply(ReplyType.IMAGE_URL, retstring)
                else:
                    reply = Reply(ReplyType.ERROR, retstring)
                return reply

    def reply_text(self, query, retry_count=0):
        try:
            result = {}
            response = self.run(query)
            result['content'] = self.get_response(response[0]) 
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
    
    def assistant_file(self,context):
        path = context.content
        filename = path[len('tmp/'):]
        msg = context.kwargs['msg']
        file_list = client.files.list(purpose='assistants')
        file = None

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
        client.beta.assistants.update(
            assistant_id=self.assistant_id,
            file_ids=[file.id]
        )
        logger.info("[OPENAI_ASSISTANT] file={} is assigned to assistant".format(context.content))
        return None
    
    def wait_on_run(self,run,thread):
        while run.status == 'queued' or run.status == 'in_progress':
            run = client.beta.threads.runs.retrieve(
                thread_id=thread.id,
                run_id=run.id
            )
            time.sleep(0.5)
        return run

    def submit_message(self,assistant_id,thread,user_message):
        client.beta.threads.messages.create(
            thread_id=thread.id,
            role='user',
            content=user_message
        )
        return client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=assistant_id
        )
    
    def get_response(self,thread):
        thread_messages =client.beta.threads.messages.list(
            thread_id=thread.id,
            order='desc'
        )        
        assistant_message = thread_messages.data[0].content[0].text.value
        return assistant_message 
    
    def run(self,user_input):
        thread = self.thread
        run = self.submit_message(self.assistant_id,thread,user_input)
        run = self.wait_on_run(run,thread)
        return thread,run
