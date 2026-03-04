from bot.session_manager import Session


class ClaudeAiSession(Session):
    def __init__(self, session_id, system_prompt=None, model="claude-haiku-4-5-20251001"):
        super().__init__(session_id, system_prompt)
        self.model = model

    def calc_tokens(self):
        """
        粗略估算当前 session 的 token 数。
        content 可能是字符串或多模态列表，统一提取文本部分计算。
        """
        total = 0
        for msg in self.messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(content)
            elif isinstance(content, list):
                for block in content:
                    if block.get("type") == "text":
                        total += len(block.get("text", ""))
                    elif block.get("type") == "image":
                        # 图片固定计一个较大的 token 估算值
                        total += 1000
                    elif block.get("type") == "document":
                        # PDF document block，按 base64 长度粗估
                        total += len(block.get("source", {}).get("data", "")) // 4
        return total

    def discard_exceeding(self, max_tokens=None, cur_tokens=None):
        """
        当历史消息超出 max_tokens 时，从最早的非 system 消息开始丢弃，
        直到 token 数满足要求。
        保证至少保留最后一轮 user 消息，不会把当前消息丢掉。
        """
        if max_tokens is None:
            return self.calc_tokens()

        while True:
            total = self.calc_tokens()
            if total <= max_tokens:
                return total

            # 找到第一条非 system 消息的索引
            first_non_system = next(
                (i for i, m in enumerate(self.messages) if m.get("role") != "system"),
                None
            )
            if first_non_system is None:
                return total

            # 至少保留最后一条 user 消息，不能再丢了
            remaining_non_system = [
                m for m in self.messages if m.get("role") != "system"
            ]
            if len(remaining_non_system) <= 1:
                return total

            self.messages.pop(first_non_system)