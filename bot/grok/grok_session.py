from bot.session_manager import Session
from xai_sdk.chat import assistant, system, user


class GrokSession(Session):
    def __init__(self, session_id, system_prompt=None, model="grok-4.20-0309-non-reasoning"):
        super().__init__(session_id, system_prompt)
        self.model = model
        self.last_total_tokens = None
        self.sdk_messages = []
        self.previous_response_id = None
        self.remote_history_outdated = False
        self.reset()

    def reset(self):
        super().reset()
        self.sdk_messages = [system(self.system_prompt)]
        self.previous_response_id = None
        self.remote_history_outdated = False

    def set_system_prompt(self, system_prompt):
        self.system_prompt = system_prompt
        self.reset()

    def add_query(self, query):
        local_content = self._normalize_local_content(query)
        user_item = {"role": "user", "content": local_content}
        self.messages.append(user_item)

        if isinstance(query, list):
            blocks = []
            for block in query:
                if isinstance(block, str):
                    blocks.append(block)
                elif isinstance(block, dict) and block.get("type") == "text":
                    blocks.append(block.get("text", ""))
                else:
                    blocks.append(block)
            self.sdk_messages.append(user(*blocks))
        else:
            self.sdk_messages.append(user(query))

    def add_reply(self, reply):
        assistant_item = {"role": "assistant", "content": reply}
        self.messages.append(assistant_item)
        self.sdk_messages.append(assistant(reply))

    def append_media_message(self, media_type, source_model):
        summary = f"[由 {source_model} 生成的{media_type}已注入会话]"
        self.sdk_messages.append(assistant(summary))
        self.remote_history_outdated = True

    def mark_remote_history_outdated(self):
        self.remote_history_outdated = True

    def _normalize_local_content(self, query):
        if not isinstance(query, list):
            return query

        normalized = []
        for block in query:
            if isinstance(block, str):
                normalized.append({"type": "text", "text": block})
                continue

            if isinstance(block, dict):
                normalized.append(block)
                continue

            image_url = self._extract_sdk_image_url(block)
            if image_url:
                normalized.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": image_url}
                    }
                )
                continue

            normalized.append({"type": "text", "text": str(block)})

        return normalized

    def _extract_sdk_image_url(self, block):
        image_url_obj = getattr(block, "image_url", None)
        if image_url_obj is None:
            return None

        direct_url = getattr(image_url_obj, "url", None)
        if direct_url:
            return direct_url

        proto_url = getattr(image_url_obj, "image_url", None)
        if proto_url:
            return proto_url

        return None

    def calc_tokens(self):
        total = 0
        for msg in self.messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(content) // 2
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, str):
                        total += len(block) // 2
                    elif isinstance(block, dict):
                        if block.get("type") == "text":
                            total += len(block.get("text", "")) // 2
                        elif block.get("type") in ("image_url", "image", "file"):
                            total += 1500
                    else:
                        # xAI SDK block对象，按多模态块做粗估，避免session_manager计数时报错
                        total += 1500
        return total

    def discard_exceeding(self, max_tokens=None, cur_tokens=None):
        if max_tokens is None:
            return self.calc_tokens()

        total = cur_tokens if cur_tokens is not None else self.calc_tokens()
        while total > max_tokens:
            first_non_system = next((i for i, msg in enumerate(self.messages) if msg.get("role") != "system"), None)
            if first_non_system is None:
                return total

            remaining_non_system = [msg for msg in self.messages if msg.get("role") != "system"]
            if len(remaining_non_system) <= 1:
                return total

            self.messages.pop(first_non_system)
            self.sdk_messages.pop(first_non_system)
            total = self.calc_tokens()
        return total
