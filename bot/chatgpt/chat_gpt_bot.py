# encoding:utf-8

import base64
import os
import time

import openai

from openai import OpenAI
from bot.bot import Bot
from bot.chatgpt.chat_gpt_session import ChatGPTSession
from bot.session_manager import SessionManager
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from common.model_status import model_state
from common.token_bucket import TokenBucket
from common import memory,const
from config import conf

_chatgpt_sessions = SessionManager(ChatGPTSession, model="gpt-3.5-turbo")

# OpenAI对话模型API (可用)
class ChatGPTBot(Bot):
    def __init__(self):
        super().__init__()
        if conf().get("rate_limit_chatgpt"):
            self.tb4chatgpt = TokenBucket(conf().get("rate_limit_chatgpt", 20))
        self.use_responses_api = conf().get("openai_use_responses_api", False)
        self.api_key = conf().get("openai_api_key")
        self.client = OpenAI(api_key=self.api_key, base_url=self._normalize_openai_base_url(conf().get("openai_api_base")))
        self.sessions = _chatgpt_sessions

    def _should_use_responses_api(self, model: str) -> bool:
        return self.use_responses_api or model in const.GPT54_LIST

    def _normalize_openai_base_url(self, base_url: str | None) -> str:
        normalized = str(base_url or "https://api.openai.com/v1").rstrip("/")
        if not normalized.endswith("/v1"):
            normalized = f"{normalized}/v1"
        return normalized

    def reply(self, query, context=None):
        try:
            session_id = context["session_id"]
            self.model = model_state.get_basic_state(session_id)
            self.Model_ID = self.model.upper()

            self.args = {
                "model": self.model,
                "temperature": conf().get("temperature", 0.9),
                "max_tokens": 4096,
                "top_p": conf().get("top_p", 1),
                "frequency_penalty": conf().get("frequency_penalty", 0.0),
                "presence_penalty": conf().get("presence_penalty", 0.0),
                "timeout": conf().get("request_timeout", None),
            }

            if context.type != ContextType.TEXT:
                return Reply(ReplyType.ERROR, f"[{self.Model_ID}]不支持处理{context.type}类型的消息")

            logger.info(f"[{self.Model_ID}] query={query}, requester={session_id}")

            media_contents = self._consume_media_contents(session_id)
            if media_contents:
                logger.info(
                    f"[{self.Model_ID}] media injection summary, "
                    f"blocks={self._summarize_user_media_blocks(media_contents)}"
                )
                media_contents.append({"type": "text", "text": query})
                query = media_contents

            session = self.sessions.build_session(session_id)
            session.model = self.model
            session = self.sessions.session_query(query, session_id)
            logger.debug(f"[{self.Model_ID}] session query={session.messages}")

            reply_content = self.reply_text(session_id, session, self.api_key, args=self.args)
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
                return Reply(ReplyType.ERROR, reply_content["content"])
            if reply_content["completion_tokens"] > 0:
                logger.info(f"[{self.Model_ID}] reply={reply_content['content']}, requester={session_id}")
                self.sessions.session_reply(reply_content["content"], session_id, reply_content["total_tokens"])
                return Reply(ReplyType.TEXT, reply_content["content"])

            logger.debug("[{}] reply {} used 0 tokens.".format(self.Model_ID, reply_content))
            return Reply(ReplyType.ERROR, reply_content["content"])
        except Exception as e:
            logger.error(f"[{self.Model_ID}] fetch reply error, {e}")
            return Reply(ReplyType.ERROR, f"[{self.Model_ID}] {e}")

    def reply_text(self,session_id:str, session: ChatGPTSession, api_key=None, args=None, retry_count=0) -> dict:
        """
        call openai's API to get the answer
        :param session: a conversation session
        :param session_id: session id
        :param retry_count: retry count
        :return: {}
        """
        try:
            if conf().get("rate_limit_chatgpt") and not self.tb4chatgpt.get_token():
                raise openai.RateLimitError("RateLimitError: rate limit exceeded")
            messages = session.messages
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

    def _create_response(self, session: ChatGPTSession, messages: list, args: dict):
        request_kwargs = {
            "model": args["model"],
            "input": self._to_response_input(messages),
            "store": True,
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
                logger.info(
                    f"[{self.Model_ID}] Responses API response_id={getattr(response, 'id', None)}, "
                    f"previous_response_id={getattr(response, 'previous_response_id', None)}"
                )
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
        logger.info(
            f"[{self.Model_ID}] Responses API response_id={getattr(response, 'id', None)}, "
            f"previous_response_id={getattr(response, 'previous_response_id', None)}"
        )
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
                if role == "assistant":
                    response_input.append(self._convert_assistant_multimodal_message(content))
                    continue
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

    def _convert_assistant_multimodal_message(self, blocks):
        normalized_blocks = self._normalize_response_content_blocks(blocks)
        has_media = any(
            isinstance(block, dict) and block.get("type") in {"input_image", "input_video", "input_file"}
            for block in normalized_blocks
        )
        if has_media:
            return {
                "role": "user",
                "content": normalized_blocks,
            }

        return {
            "role": "assistant",
            "content": self._summarize_assistant_multimodal_content(blocks),
        }

    def _summarize_assistant_multimodal_content(self, blocks):
        text_parts = []
        image_count = 0
        video_count = 0
        file_count = 0

        for block in blocks:
            if not isinstance(block, dict):
                text_parts.append(str(block))
                continue

            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text", "")
                if text:
                    text_parts.append(text)
            elif block_type == "image_url":
                image_count += 1
            elif block_type == "video_url":
                video_count += 1
            elif block_type == "file":
                file_count += 1

        summary_parts = []
        if image_count:
            summary_parts.append(f"[assistant injected {image_count} image]")
        if video_count:
            summary_parts.append(f"[assistant injected {video_count} video]")
        if file_count:
            summary_parts.append(f"[assistant injected {file_count} file]")
        summary_parts.extend(text_parts)
        return "\n".join(summary_parts) if summary_parts else "[assistant multimodal content]"

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
            elif block_type == "video_url":
                video_payload = block.get("video_url", {})
                video_url = video_payload.get("url", "")
                if video_url:
                    normalized.append({
                        "type": "input_video",
                        "video_url": video_url,
                        "fps": video_payload.get("fps", 1),
                    })
            elif block_type == "file":
                file_content = self._normalize_file_block(block.get("file", {}))
                if file_content:
                    normalized.append(file_content)
            else:
                normalized.append({
                    "type": "input_text",
                    "text": str(block),
                })
        return normalized

    def _consume_media_contents(self, session_id):
        image_cache = memory.USER_IMAGE_CACHE.get(session_id)
        video_cache = memory.USER_VIDEO_CACHE.get(session_id)
        file_cache = memory.USER_FILE_CACHE.get(session_id)
        quoted_image_cache = memory.USER_QUOTED_IMAGE_CACHE.get(session_id)
        quoted_file_cache = memory.USER_QUOTED_FILE_CACHE.get(session_id)

        logger.info(
            f"[{self.Model_ID}] cache summary before request, "
            f"quoted_files={len((quoted_file_cache or {}).get('files', []))}, "
            f"files={len((file_cache or {}).get('files', []))}, "
            f"quoted_images={len((quoted_image_cache or {}).get('files', []))}, "
            f"images={len((image_cache or {}).get('files', []))}, "
            f"videos={len((video_cache or {}).get('files', []))}"
        )

        media_contents = []
        if quoted_file_cache:
            quoted_file_contents = self._build_file_contents(quoted_file_cache)
            if quoted_file_contents:
                media_contents.extend(quoted_file_contents)
                logger.info(f"[{self.Model_ID}] 从引用回复文档缓存取内容, count={len(quoted_file_contents)}")
            memory.USER_QUOTED_FILE_CACHE.pop(session_id, None)
        if file_cache:
            file_contents = self._build_file_contents(file_cache)
            if file_contents:
                media_contents.extend(file_contents)
                logger.info(f"[{self.Model_ID}] 从内存文档缓存取内容, count={len(file_contents)}")
            memory.USER_FILE_CACHE.pop(session_id, None)
        if quoted_image_cache:
            quoted_image_contents = self._build_image_contents(quoted_image_cache)
            if quoted_image_contents:
                media_contents.extend(quoted_image_contents)
                logger.info(f"[{self.Model_ID}] 从引用回复图片缓存取内容, count={len(quoted_image_contents)}")
            memory.USER_QUOTED_IMAGE_CACHE.pop(session_id, None)
        if image_cache:
            image_contents = self._build_image_contents(image_cache)
            if image_contents:
                media_contents.extend(image_contents)
                logger.info(f"[{self.Model_ID}] 从内存参考图取内容, count={len(image_contents)}")
            memory.USER_IMAGE_CACHE.pop(session_id, None)
        if video_cache:
            video_contents = self._build_video_contents(video_cache)
            if video_contents:
                media_contents.extend(video_contents)
                logger.info(f"[{self.Model_ID}] 从内存参考视频取内容, count={len(video_contents)}")
            memory.USER_VIDEO_CACHE.pop(session_id, None)
        return media_contents

    def _build_image_contents(self, image_cache):
        if not image_cache:
            return []

        contents = []
        image_paths = image_cache.get("path", [])
        image_files = image_cache.get("files", [])
        for image_path, image_file in zip(image_paths, image_files):
            data_url = self._encode_image_file(image_path, image_file)
            if data_url:
                contents.append({
                    "type": "image_url",
                    "image_url": {"url": data_url},
                })
        return contents

    def _build_video_contents(self, video_cache):
        contents = []
        for video_file in video_cache.get("files", []):
            if not isinstance(video_file, dict):
                logger.warning(f"[{self.Model_ID}] unsupported cached video type: {type(video_file).__name__}")
                continue
            video_path = video_file.get("path")
            if not video_path:
                logger.warning(f"[{self.Model_ID}] cached video missing path")
                continue
            data_url = self._encode_video_file(video_path, video_file)
            if data_url:
                contents.append({
                    "type": "video_url",
                    "video_url": {
                        "url": data_url,
                        "fps": 1,
                    }
                })
        return contents

    def _build_file_contents(self, file_cache):
        contents = []
        for cached_file in file_cache.get("files", []):
            if not isinstance(cached_file, dict):
                logger.warning(f"[{self.Model_ID}] unsupported cached file type: {type(cached_file).__name__}")
                continue

            mime_type = cached_file.get("mime_type", "")
            file_path = cached_file.get("path", "")
            file_name = os.path.basename(file_path) if file_path else "unknown"
            file_size = os.path.getsize(file_path) if file_path and os.path.exists(file_path) else 0
            logger.info(
                f"[{self.Model_ID}] preparing file block, "
                f"name={file_name}, mime_type={mime_type}, size={file_size}"
            )

            if mime_type == "application/pdf":
                raw_data = cached_file.get("data", "")
                if raw_data:
                    contents.append({
                        "type": "file",
                        "file": {
                            "filename": file_name,
                            "mime_type": mime_type,
                            "file_data": f"data:{mime_type};base64,{raw_data}",
                        },
                    })
                continue

            if mime_type in ("application/docx", "application/doc", "application/plain", "application/txt"):
                raw_text = cached_file.get("data", "")
                if raw_text:
                    contents.append({
                        "type": "text",
                        "text": f"<document>\n{raw_text}\n</document>",
                    })
                    logger.info(
                        f"[{self.Model_ID}] document text block added, "
                        f"name={file_name}, mime_type={mime_type}, text_length={len(raw_text)}"
                    )
                continue

            logger.warning(f"[{self.Model_ID}] unsupported file mime_type: {mime_type}")
        return contents

    def _normalize_file_block(self, file_payload):
        file_id = file_payload.get("file_id")
        file_name = file_payload.get("filename", "upload.pdf")
        file_data = file_payload.get("file_data")

        normalized = {
            "type": "input_file",
        }
        if file_id:
            normalized["file_id"] = file_id
        elif file_data:
            normalized["filename"] = file_name
            normalized["file_data"] = file_data
        else:
            return None
        return normalized

    def _encode_image_file(self, image_path, image_file):
        try:
            image_type = type(image_file).__name__
            mime_type = "image/png" if image_type == "PngImageFile" else "image/jpeg"
            with open(image_path, "rb") as file:
                base64_image = base64.b64encode(file.read()).decode("utf-8")
            return f"data:{mime_type};base64,{base64_image}"
        except Exception as e:
            logger.warning(f"[{self.Model_ID}] failed to encode image {image_path}: {e}")
            return None

    def _encode_video_file(self, video_path, video_file):
        try:
            public_url = video_file.get("public_url") if isinstance(video_file, dict) else None
            if public_url:
                return public_url
            mime_type = video_file.get("mime_type") if isinstance(video_file, dict) else None
            if not mime_type:
                suffix = video_path.rsplit(".", 1)[-1].lower() if "." in video_path else "mp4"
                mime_type = f"video/{suffix}"
            with open(video_path, "rb") as file:
                base64_video = base64.b64encode(file.read()).decode("utf-8")
            return f"data:{mime_type};base64,{base64_video}"
        except Exception as e:
            logger.warning(f"[{self.Model_ID}] failed to encode video {video_path}: {e}")
            return None

    def _summarize_user_media_blocks(self, blocks):
        summary = {"text": 0, "image": 0, "video": 0, "file": 0}
        for block in blocks:
            if not isinstance(block, dict):
                summary["text"] += 1
                continue
            block_type = block.get("type")
            if block_type == "text":
                summary["text"] += 1
            elif block_type == "image_url":
                summary["image"] += 1
            elif block_type == "video_url":
                summary["video"] += 1
            elif block_type == "file":
                summary["file"] += 1
        return summary

    def _extract_response_text(self, response) -> str:
        output_text = getattr(response, "output_text", None)
        if output_text:
            return output_text

        collected = []
        for item in getattr(response, "output", []) or []:
            item_type = self._get_attr_or_key(item, "type")
            if item_type != "message":
                continue

            for content in self._get_attr_or_key(item, "content", []) or []:
                text = self._extract_text_from_content(content)
                if text:
                    collected.append(text)

        return "".join(collected)

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

    def _extract_text_from_content(self, content):
        content_type = self._get_attr_or_key(content, "type")
        if content_type in {"output_text", "text", "input_text"}:
            text = self._get_attr_or_key(content, "text")
            if isinstance(text, str):
                return text
            if hasattr(text, "value"):
                return getattr(text, "value", "")

        text_obj = self._get_attr_or_key(content, "text")
        if isinstance(text_obj, str):
            return text_obj
        if hasattr(text_obj, "value"):
            return getattr(text_obj, "value", "")
        return ""

    def _get_attr_or_key(self, data, name, default=None):
        if isinstance(data, dict):
            return data.get(name, default)
        return getattr(data, name, default)
