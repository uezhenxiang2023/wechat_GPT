"""
Bytedance volcengine_ark bot

@author fort
@Date 2025/10/19
"""

import json

from volcenginesdkarkruntime import Ark

from config import conf
from bot.bot import Bot
from bot.ark.ark_media import process_image_files, process_video_files
from bot.session_manager import SessionManager
from bot.ark.volcengine_ark_session import VolcengineArkSession
from bridge.context import ContextType, Context
from bridge.reply import Reply, ReplyType
from common import const, memory
from common.log import logger
from common.model_status import model_state

_ark_sessions = SessionManager(VolcengineArkSession, model=const.DOUBAO_SEED_20)

class VolcengineArkBot(Bot):

    def __init__(self):
        super().__init__()
        self.api_key = conf().get("ark_api_key")
        self.system_prompt = conf().get("character_desc") 
        self.use_responses_api = conf().get("ark_use_responses_api", False)
        self.sessions = _ark_sessions

        self.client = Ark(
            api_key=self.api_key
        )

    def reply(self, query, context: Context = None) -> Reply:
        try:
            session_id = context["session_id"]
            self.model = model_state.get_basic_state(session_id)
            self.Model_ID = self.model.upper()

            if context.type != ContextType.TEXT:
                return Reply(ReplyType.ERROR, "Only text context is supported")

            logger.info(f"[{self.Model_ID}] query={query}, requester={session_id}")

            # 检查缓存中是否媒体文件
            image_cache = memory.USER_IMAGE_CACHE.get(session_id)
            video_cache = memory.USER_VIDEO_CACHE.get(session_id)
            file_cache = memory.USER_FILE_CACHE.get(session_id)

            media_contents = []
            if file_cache:
                file_count = len(file_cache.get("files", [])) if isinstance(file_cache.get("files"), list) else 1
                logger.info(f"[{self.Model_ID}] 从内存文档缓存取内容, count={file_count}")
                return Reply(ReplyType.ERROR, "Ark 文档缓存已归位到 USER_FILE_CACHE，文档消息体结构下一步再补。")
            if image_cache:
                image_contents = process_image_files(image_cache)
                media_contents.extend(image_contents)
                logger.info(f"[{self.Model_ID}] 从内存参考图取内容, count={len(image_contents)}")
                memory.USER_IMAGE_CACHE.pop(session_id)
            if video_cache:
                video_contents = process_video_files(video_cache)
                media_contents.extend(video_contents)
                logger.info(f"[{self.Model_ID}] 从内存参考视频取内容, count={len(video_contents)}")
                memory.USER_VIDEO_CACHE.pop(session_id)
            if media_contents:
                media_contents.append({"type": "text", "text": query})
                query = media_contents

            session = self.sessions.session_query(query, session_id)
            if self.use_responses_api and self.model not in const.DOUBAO_BOT_LIST:
                response = self._create_response(session, query)
                reply_text = self._extract_response_text(response)
                total_tokens = self._extract_response_total_tokens(response)
                session.previous_response_id = getattr(response, "id", None)
                session.remote_history_outdated = False
                logger.info(
                    f"[{self.Model_ID}] Responses API response_id={getattr(response, 'id', None)}, "
                    f"previous_response_id={getattr(response, 'previous_response_id', None)}, requester={session_id}"
                )
            else:
                client_attr = 'bot_chat' if self.model in const.DOUBAO_BOT_LIST else 'chat'
                # 如果调用bot,去掉messages列表中的system prompt
                if client_attr == 'bot_chat' and session.messages[0].get('role') == 'system':
                    session.messages.pop(0)
                completion = getattr(self.client, client_attr).completions.create(
                    model=self.model,
                    messages=session.messages,
                    #thinking={"type": self.thinking}
                )
                reply_text = completion.choices[0].message.content
                total_tokens = completion.usage.total_tokens if client_attr == 'chat' else completion.bot_usage.model_usage[0].total_tokens
            logger.info(f"[{self.Model_ID}] reply={reply_text}, requester={session_id}")
            self.sessions.session_reply(reply_text, session_id, total_tokens)
            return Reply(ReplyType.TEXT, reply_text)

        except Exception as e:
            logger.error(f"[{self.Model_ID}] fetch reply error, {e}")
            return Reply(ReplyType.ERROR, f"[{self.Model_ID}] {e}")

    def _create_response(self, session, current_query):
        request_kwargs = {
            "model": self.model,
            "store": True,
        }
        if session.previous_response_id and not session.remote_history_outdated:
            request_kwargs["input"] = self._to_response_input([{"role": "user", "content": current_query}])
            request_kwargs["previous_response_id"] = session.previous_response_id
            try:
                return self.client.responses.create(**request_kwargs)
            except Exception as e:
                logger.warning(
                    f"[{self.Model_ID}] Responses API continuation failed, fallback to local history replay: {e}"
                )
                session.previous_response_id = None
                session.remote_history_outdated = True

        request_kwargs["input"] = self._to_response_input(session.messages)
        request_kwargs.pop("previous_response_id", None)
        return self.client.responses.create(**request_kwargs)

    def _to_response_input(self, messages):
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
                    "content": self._normalize_content_blocks(content),
                })
                continue

            response_input.append({
                "role": role,
                "content": str(content),
            })
        return response_input

    def _convert_assistant_multimodal_message(self, blocks):
        normalized_blocks = self._normalize_content_blocks(blocks)
        has_media = any(
            isinstance(block, dict) and block.get("type") in {"input_image", "input_video"}
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

        summary_parts = []
        if image_count:
            summary_parts.append(f"[assistant injected {image_count} image]")
        if video_count:
            summary_parts.append(f"[assistant injected {video_count} video]")
        summary_parts.extend(text_parts)
        return "\n".join(summary_parts) if summary_parts else "[assistant multimodal content]"

    def _normalize_content_blocks(self, blocks):
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
                continue

            if block_type == "image_url":
                image_url = block.get("image_url", {}).get("url")
                if image_url:
                    normalized.append({
                        "type": "input_image",
                        "image_url": image_url,
                    })
                continue

            if block_type == "video_url":
                video_payload = block.get("video_url", {})
                video_url = video_payload.get("url")
                if video_url:
                    normalized.append({
                        "type": "input_video",
                        "video_url": video_url,
                        "fps": video_payload.get("fps", 1),
                    })
                continue

            normalized.append(block)
        return normalized

    def _extract_response_text(self, response):
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

        if collected:
            return "".join(collected)

        try:
            return json.dumps(response, ensure_ascii=False)
        except TypeError:
            return str(response)

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

    def _extract_response_total_tokens(self, response):
        usage = getattr(response, "usage", None)
        if usage is None:
            return None

        total_tokens = getattr(usage, "total_tokens", None)
        if total_tokens is not None:
            return total_tokens

        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        total = input_tokens + output_tokens
        return total if total else None

    def _get_attr_or_key(self, data, name, default=None):
        if isinstance(data, dict):
            return data.get(name, default)
        return getattr(data, name, default)
