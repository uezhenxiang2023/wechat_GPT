# encoding:utf-8

import base64
import time

import openai
import requests

from openai import OpenAI
from bot.bot import Bot
from bot.chatgpt.chat_gpt_session import ChatGPTSession
from bot.session_manager import SessionManager
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from common.model_status import model_state
from common.token_bucket import TokenBucket
from common import memory,utils,const
from config import conf, load_config
from PIL import Image

# OpenAI对话模型API (可用)
class ChatGPTBot(Bot):
    def __init__(self):
        super().__init__()
        if conf().get("rate_limit_chatgpt"):
            self.tb4chatgpt = TokenBucket(conf().get("rate_limit_chatgpt", 20))
        self.use_responses_api = conf().get("openai_use_responses_api", False)
        self.api_key = conf().get("openai_api_key")
        self.client = OpenAI(api_key=self.api_key, base_url=self._normalize_openai_base_url(conf().get("openai_api_base")))

        self.sessions = SessionManager(ChatGPTSession, model="gpt-3.5-turbo")
        self.args = {
            "model": "gpt-3.5-turbo",  # 对话模型的名称，由当前用户 model_state 覆盖
            "temperature": conf().get("temperature", 0.9),  # 值在[0,1]之间，越大表示回复越具有不确定性
            "max_tokens":4096,  # 回复最大的字符数
            "top_p": conf().get("top_p", 1),
            "frequency_penalty": conf().get("frequency_penalty", 0.0),  # [-2,2]之间，该值越大则更倾向于产生不同的内容
            "presence_penalty": conf().get("presence_penalty", 0.0),  # [-2,2]之间，该值越大则更倾向于产生不同的内容
            "timeout": conf().get("request_timeout", None),  # 重试超时时间，在这个时间内，将会自动重试
        }

    def _should_use_responses_api(self, model: str) -> bool:
        return self.use_responses_api or model in const.GPT54_LIST

    def _normalize_openai_base_url(self, base_url: str | None) -> str:
        normalized = str(base_url or "https://api.openai.com/v1").rstrip("/")
        if not normalized.endswith("/v1"):
            normalized = f"{normalized}/v1"
        return normalized

    def reply(self, query, context=None):
        session_id = context["session_id"]
        self.model = model_state.get_basic_state(session_id)
        self.Model_ID = self.model.upper()

        if context.type != ContextType.TEXT:
            return Reply(ReplyType.ERROR, "[{}]不支持处理{}类型的消息".format(self.Model_ID, context.type))

        logger.info("[{}] query={}".format(self.Model_ID, query))

        session = self.sessions.build_session(session_id)
        session.model = self.model
        session = self.sessions.session_query(query, session_id)
        logger.debug("[CHATGPT] session query={}".format(session.messages))

        model = context.get("gpt_model") or self.model
        new_args = None
        if model:
            new_args = self.args.copy()
            new_args["model"] = model

        reply_content = self.reply_text(session_id,session, self.api_key, args=new_args)
        logger.debug(
            "[{}] new_query={}, session_id={}, reply_cont={}, completion_tokens={}".format(
                self.Model_ID,
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
            logger.debug("[{}] reply {} used 0 tokens.".format(self.Model_ID, reply_content))
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
            if args['model'] == const.GPT4_VISION_PREVIEW:  
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
                response = self.base64_image_request(messages, args)
                return {
                    "total_tokens": response.json()['usage']['total_tokens'],
                    "completion_tokens": response.json()['usage']['completion_tokens'],
                    "content": response.json()['choices'][0]['message']['content']
                }
            else:
                if self._should_use_responses_api(args["model"]):
                    try:
                        response = self._create_response(session, messages, args)
                        return {
                            "total_tokens": self._extract_response_total_tokens(response),
                            "completion_tokens": self._extract_response_output_tokens(response),
                            "content": self._extract_response_text(response),
                        }
                    except openai.NotFoundError as e:
                        logger.warning(
                            "[{}] Responses API not supported by current upstream, fallback to Chat Completions: {}".format(
                                self.Model_ID, e
                            )
                        )
                        session.previous_response_id = None
                        session.remote_history_outdated = True
                    except openai.APIError as e:
                        if "404" in str(e):
                            logger.warning(
                                "[{}] Responses API returned 404, fallback to Chat Completions: {}".format(
                                    self.Model_ID, e
                                )
                            )
                            session.previous_response_id = None
                            session.remote_history_outdated = True
                        else:
                            raise
                response = self.client.chat.completions.create(messages=messages, **args)
                return {
                    "total_tokens": response.usage.total_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "content": response.choices[0].message.content,
                }
        except Exception as e:
            need_retry = retry_count < 2
            result = {"completion_tokens": 0, "content": "我现在有点累了，等会再来吧"}
            if isinstance(e, openai.RateLimitError):
                logger.warn("[{}] RateLimitError: {}".format(self.model, e))
                result["content"] = "提问太快啦，请休息一下再问我吧"
                if need_retry:
                    time.sleep(20)
            elif isinstance(e, openai.Timeout):
                logger.warn("[{}] Timeout: {}".format(self.model, e))
                result["content"] = "我没有收到你的消息"
                if need_retry:
                    time.sleep(5)
            elif isinstance(e, openai.APIError):
                logger.warn("[{}] Bad Gateway: {}".format(self.model, e))
                result["content"] = "请再问我一次"
                if need_retry:
                    time.sleep(10)
            elif isinstance(e, openai.APIConnectionError):
                logger.warn("[{}] APIConnectionError: {}".format(self.model, e))
                result["content"] = "我连接不到你的网络"
                if need_retry:
                    time.sleep(5)
            else:
                logger.exception("[{}] Exception: {}".format(self.model, e))
                need_retry = False
                self.sessions.clear_session(session.session_id)

            if need_retry:
                logger.warn("[{}] 第{}次重试".format(self.model, retry_count + 1))
                return self.reply_text(session_id,session, api_key, args, retry_count + 1)
            else:
                return result
            
    def gpt4_vision(self, query, context, session: ChatGPTSession):
        session_id = context.kwargs["session_id"]
        msg = context.kwargs["msg"]
        img_path = context.content
        logger.info(f"[{self.model}] query with images, path={img_path}")
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
    
    def base64_image_request(self, messages: list, args=None):
        if args is None:
            args = self.args
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.client.api_key}"
        }
        payload = {
            'model': args['model'],
            'max_tokens': args['max_tokens'],
            'messages': messages
        }
        base_url = self._normalize_openai_base_url(
            conf().get("openai_api_base") or conf().get("open_ai_api_base")
        )
        response = requests.post(f"{base_url}/chat/completions", headers=headers, json=payload)
        return response

    def _create_response(self, session: ChatGPTSession, messages: list, args: dict):
        request_kwargs = {
            "model": args["model"],
            "input": self._to_response_input(messages),
        }
        temperature = args.get("temperature")
        if temperature is not None and args["model"] not in const.GPT54_LIST:
            request_kwargs["temperature"] = temperature
        max_output_tokens = args.get("max_tokens")
        if max_output_tokens is not None:
            request_kwargs["max_output_tokens"] = max_output_tokens

        logger.info(
            "[{}] call Responses API, base_url={}, use_config_flag={}".format(
                self.Model_ID,
                self._normalize_openai_base_url(conf().get("openai_api_base")),
                conf().get("openai_use_responses_api", False),
            )
        )

        if session.previous_response_id and not session.remote_history_outdated:
            request_kwargs["input"] = self._to_response_input([messages[-1]])
            request_kwargs["previous_response_id"] = session.previous_response_id
            try:
                response = self.client.responses.create(**request_kwargs)
                session.previous_response_id = getattr(response, "id", None)
                session.remote_history_outdated = False
                return response
            except Exception as e:
                logger.warning(
                    "[{}] Responses API continuation failed, fallback to local history replay: {}".format(
                        self.Model_ID, e
                    )
                )
                session.previous_response_id = None
                session.remote_history_outdated = True

        request_kwargs.pop("previous_response_id", None)
        response = self.client.responses.create(**request_kwargs)
        session.previous_response_id = getattr(response, "id", None)
        session.remote_history_outdated = False
        return response

    def _to_response_input(self, messages: list):
        response_input = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            if isinstance(content, str):
                response_input.append({
                    "role": role,
                    "content": content,
                })
                continue

            if isinstance(content, list):
                response_input.append({
                    "role": role,
                    "content": self._normalize_response_content_blocks(content),
                })
                continue

            if isinstance(content, dict):
                response_input.append({
                    "role": role,
                    "content": self._normalize_response_content_blocks([content]),
                })
                continue

            response_input.append({
                "role": role,
                "content": str(content),
            })
        return response_input

    def _normalize_response_content_blocks(self, blocks: list):
        normalized = []
        for block in blocks:
            if not isinstance(block, dict):
                normalized.append({"type": "input_text", "text": str(block)})
                continue

            block_type = block.get("type")
            if block_type == "text":
                normalized.append({
                    "type": "input_text",
                    "text": block.get("text", ""),
                })
            elif block_type == "image_url":
                normalized.append({
                    "type": "input_image",
                    "image_url": block.get("image_url", {}).get("url", ""),
                })
            else:
                normalized.append({
                    "type": "input_text",
                    "text": str(block),
                })
        return normalized

    def _extract_response_text(self, response) -> str:
        output_text = getattr(response, "output_text", None)
        if output_text:
            return output_text

        text_parts = []
        for item in getattr(response, "output", []) or []:
            for block in getattr(item, "content", []) or []:
                if getattr(block, "type", None) in ("output_text", "text"):
                    text = getattr(block, "text", "")
                    if text:
                        text_parts.append(text)
        return "".join(text_parts)

    def _extract_response_total_tokens(self, response) -> int:
        usage = getattr(response, "usage", None)
        total_tokens = getattr(usage, "total_tokens", None)
        if total_tokens is not None:
            return total_tokens
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        return input_tokens + output_tokens

    def _extract_response_output_tokens(self, response) -> int:
        usage = getattr(response, "usage", None)
        output_tokens = getattr(usage, "output_tokens", None)
        if output_tokens is not None:
            return output_tokens
        return 0
