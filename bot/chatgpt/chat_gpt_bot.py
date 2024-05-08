# encoding:utf-8

import base64
import time

import openai
import requests

from openai import OpenAI
from bot.bot import Bot
from bot.chatgpt.chat_gpt_session import ChatGPTSession
from bot.openai.open_ai_image import OpenAIImage
from bot.openai.open_ai_vision import OpenAIVision
from bot.session_manager import SessionManager
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from common.token_bucket import TokenBucket
from common import memory,utils,const
from config import conf, load_config
from PIL import Image

client = OpenAI(api_key=conf().get("open_ai_api_key")) #Instantiate a client according to latest openai SDK

# OpenAI对话模型API (可用)
class ChatGPTBot(Bot,OpenAIImage,OpenAIVision):
    def __init__(self):
        super().__init__()
        # set the default endpoint of curl request
        if conf().get("open_ai_api_base"):
            openai.base_url = conf().get("open_ai_api_base")
        proxy = conf().get("proxy")
        if proxy:
            openai.ProxiesTypes = proxy
        if conf().get("rate_limit_chatgpt"):
            self.tb4chatgpt = TokenBucket(conf().get("rate_limit_chatgpt", 20))

        self.sessions = SessionManager(ChatGPTSession, model=conf().get("model") or "gpt-3.5-turbo")
        self.args = {
            "model": conf().get("model") or "gpt-3.5-turbo",  # 对话模型的名称
            "temperature": conf().get("temperature", 0.9),  # 值在[0,1]之间，越大表示回复越具有不确定性
            "max_tokens":4096,  # 回复最大的字符数
            "top_p": conf().get("top_p", 1),
            "frequency_penalty": conf().get("frequency_penalty", 0.0),  # [-2,2]之间，该值越大则更倾向于产生不同的内容
            "presence_penalty": conf().get("presence_penalty", 0.0),  # [-2,2]之间，该值越大则更倾向于产生不同的内容
            "timeout": conf().get("request_timeout", None),  # 重试超时时间，在这个时间内，将会自动重试
        }

    def reply(self, query, context=None):
        # acquire reply content
        if context.type == ContextType.TEXT:
            logger.info("[CHATGPT] query={}".format(query))

            session_id = context["session_id"]
            reply = None
            clear_memory_commands = conf().get("clear_memory_commands", ["#清除记忆"])
            if query in clear_memory_commands:
                self.sessions.clear_session(session_id)
                reply = Reply(ReplyType.INFO, "记忆已清除")
            elif query == "#清除所有":
                self.sessions.clear_all_session()
                reply = Reply(ReplyType.INFO, "所有人记忆已清除")
            elif query == "#更新配置":
                load_config()
                reply = Reply(ReplyType.INFO, "配置已更新")
            if reply:
                return reply
            session = self.sessions.session_query(query, session_id)
            logger.debug("[CHATGPT] session query={}".format(session.messages))

            api_key = context.get("openai_api_key")
            model = context.get("gpt_model")
            new_args = None
            if model:
                new_args = self.args.copy()
                new_args["model"] = model
            # if context.get('stream'):
            #     # reply in stream
            #     return self.reply_text_stream(query, new_query, session_id)
            if self.args['model'] == const.GPT4_TURBO and query[:8] == 'https://':
                #and any(query[n:] in ['jpg', 'png', 'webp', 'gif'] for n in (-3, -4))
                return self.gpt4_turbo_vision(query, context, session)
            
            reply_content = self.reply_text(session_id,session, api_key, args=new_args)
            logger.debug(
                "[CHATGPT] new_query={}, session_id={}, reply_cont={}, completion_tokens={}".format(
                    session.messages,
                    session_id,
                    reply_content["content"],
                    reply_content["completion_tokens"],
                )
            )
            if reply_content["completion_tokens"] == 0 and len(reply_content["content"]) > 0:
                reply = Reply(ReplyType.ERROR, reply_content["content"])
            elif reply_content["completion_tokens"] > 0:
                self.sessions.session_reply(reply_content["content"], session_id, reply_content["total_tokens"])
                reply = Reply(ReplyType.TEXT, reply_content["content"])
            else:
                reply = Reply(ReplyType.ERROR, reply_content["content"])
                logger.debug("[CHATGPT] reply {} used 0 tokens.".format(reply_content))
            return reply

        elif context.type == ContextType.IMAGE_CREATE:
            ok, retstring = self.create_img(query, 0)
            reply = None
            if ok:
                reply = Reply(ReplyType.IMAGE_URL, retstring)
            else:
                reply = Reply(ReplyType.ERROR, retstring)
            return reply
        elif context.type == ContextType.IMAGE and self.args['model'] == const.GPT4_TURBO:
            session_id = context["session_id"]
            session = self.sessions.session_query(query, session_id)
            return self.gpt4_turbo_vision(query, context, session)
        else:
            reply = Reply(ReplyType.ERROR, "Bot不支持处理{}类型的消息".format(context.type))
            return reply

    def reply_text(self,session_id:str, session: ChatGPTSession, api_key=None, args=None, retry_count=0) -> dict:
        """
        call openai's ChatCompletion to get the answer
        :param session: a conversation session
        :param session_id: session id
        :param retry_count: retry count
        :return: {}
        """
        try:
            if conf().get("rate_limit_chatgpt") and not self.tb4chatgpt.get_token():
                raise openai.RateLimitError("RateLimitError: rate limit exceeded")
            # if api_key == None, the default openai.api_key will be used
            if args is None:
                args = self.args
            # Image recongnition and vision completion with gpt-4-vision-preview
            if args['model'] != const.GPT4_TURBO:  
                vision_res = self.do_vision_completion_if_need(session_id,session.messages[-1]['content'])
                if vision_res:
                    return vision_res
            if type(session.messages[-2]['content']).__name__ == 'dict':
                messages = self._convert_to_gpt4_turbo_messages(session.messages)
                self.sessions.clear_session(session_id)
            else:
                messages = session.messages

            # Vision request by base64
            content_type = type(messages[-1].get('content')).__name__ 
            if content_type == 'list' and \
                messages[-1]['content'][0]['image_url']['url'][:8] != 'https://':
                response = self.base64_image_request(messages)
                return {
                    "total_tokens": response.json()['usage']['total_tokens'],
                    "completion_tokens": response.json()['usage']['completion_tokens'],
                    "content": response.json()['choices'][0]['message']['content']
                }
            else:
                response = client.chat.completions.create(messages=messages, **args)
            # logger.debug("[CHATGPT] response={}".format(response))
            # logger.info("[ChatGPT] reply={}, total_tokens={}".format(response.choices[0]['message']['content'], response["usage"]["total_tokens"]))
                return {
                    "total_tokens": response.usage.total_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "content": response.choices[0].message.content,
                }
        except Exception as e:
            need_retry = retry_count < 2
            result = {"completion_tokens": 0, "content": "我现在有点累了，等会再来吧"}
            if isinstance(e, openai.RateLimitError):
                logger.warn("[CHATGPT] RateLimitError: {}".format(e))
                result["content"] = "提问太快啦，请休息一下再问我吧"
                if need_retry:
                    time.sleep(20)
            elif isinstance(e, openai.Timeout):
                logger.warn("[CHATGPT] Timeout: {}".format(e))
                result["content"] = "我没有收到你的消息"
                if need_retry:
                    time.sleep(5)
            elif isinstance(e, openai.APIError):
                logger.warn("[CHATGPT] Bad Gateway: {}".format(e))
                result["content"] = "请再问我一次"
                if need_retry:
                    time.sleep(10)
            elif isinstance(e, openai.APIConnectionError):
                logger.warn("[CHATGPT] APIConnectionError: {}".format(e))
                result["content"] = "我连接不到你的网络"
                if need_retry:
                    time.sleep(5)
            else:
                logger.exception("[CHATGPT] Exception: {}".format(e))
                need_retry = False
                self.sessions.clear_session(session.session_id)

            if need_retry:
                logger.warn("[CHATGPT] 第{}次重试".format(retry_count + 1))
                return self.reply_text(session_id,session, api_key, args, retry_count + 1)
            else:
                return result
            
    def gpt4_turbo_vision(self, query, context, session: ChatGPTSession):
        session_id = context.kwargs["session_id"]
        msg = context.kwargs["msg"]
        img_path = context.content
        logger.info(f"[GPT4_TURBO] query with images, path={img_path}")
        # Image URL request
        if query[:8] == 'https://':
            image_prompt = img_path
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
                binary_data = image_file.read()
                base_64_encoded_data = base64.b64encode(binary_data)
                base64_string = base_64_encoded_data.decode('utf-8')
                image_prompt = f'data:image/jpeg; base64, {base64_string}'
        image_query = {"type": 'image_url', 'image_url': {"url": image_prompt}}
        self.sessions.session_query(image_query, session_id)
    
    def _convert_to_gpt4_turbo_messages(self, messages: list):
        res = []
        system_content = messages.pop(0)
        res.append(system_content)
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
        res.append({'role': 'user', 'content': content})
        return res
    
    def base64_image_request(self,messages:list):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {client.api_key}"
        }
        payload = {
            'model': self.args['model'],
            'max_tokens': self.args['max_tokens'],
            'messages': messages
        }
        response = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
        return response


class AzureChatGPTBot(ChatGPTBot):
    def __init__(self):
        super().__init__()
        openai.api_type = "azure"
        openai.api_version = conf().get("azure_api_version", "2023-06-01-preview")
        self.args["deployment_id"] = conf().get("azure_deployment_id")

    def create_img(self, query, retry_count=0, api_key=None):
        api_version = "2022-08-03-preview"
        url = "{}dalle/text-to-image?api-version={}".format(openai.base_url, api_version)
        api_key = api_key or openai.api_key
        headers = {"api-key": api_key, "Content-Type": "application/json"}
        try:
            body = {"caption": query, "resolution": conf().get("image_create_size", "256x256")}
            submission = requests.post(url, headers=headers, json=body)
            operation_location = submission.headers["Operation-Location"]
            retry_after = submission.headers["Retry-after"]
            status = ""
            image_url = ""
            while status != "Succeeded":
                logger.info("waiting for image create..., " + status + ",retry after " + retry_after + " seconds")
                time.sleep(int(retry_after))
                response = requests.get(operation_location, headers=headers)
                status = response.json()["status"]
            image_url = response.json()["result"]["contentUrl"]
            return True, image_url
        except Exception as e:
            logger.error("create image error: {}".format(e))
            return False, "图片生成失败"
