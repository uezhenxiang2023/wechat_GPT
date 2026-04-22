from bot.session_manager import Session
from common.log import logger
from common import const

"""
    e.g.  [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Who won the world series in 2020?"},
        {"role": "assistant", "content": "The Los Angeles Dodgers won the World Series in 2020."},
        {"role": "user", "content": "Where was it played?"}
    ]
"""


class ChatGPTSession(Session):
    def __init__(self, session_id, system_prompt=None, model="gpt-3.5-turbo"):
        super().__init__(session_id, system_prompt)
        self.model = model
        self.last_total_tokens = None
        self.previous_response_id = None
        self.remote_history_outdated = False
        self.reset()

    def reset(self):
        super().reset()
        self.previous_response_id = None
        self.remote_history_outdated = False

    def set_system_prompt(self, system_prompt):
        self.system_prompt = system_prompt
        self.reset()

    def mark_remote_history_outdated(self):
        self.remote_history_outdated = True

    def discard_exceeding(self, max_tokens, cur_tokens=None):
        total = cur_tokens if cur_tokens is not None else (
            self.last_total_tokens + self._calc_last_message_tokens()
            if self.last_total_tokens is not None
            else self.calc_tokens()
        )

        while True:
            if total <= max_tokens:
                return total

            first_non_system = next(
                (i for i, m in enumerate(self.messages) if m.get("role") != "system"),
                None
            )
            if first_non_system is None:
                return total

            remaining_non_system = [
                m for m in self.messages if m.get("role") != "system"
            ]
            if len(remaining_non_system) <= 1:
                logger.warning("user message exceed max_tokens. total_tokens={}".format(total))
                return total

            self.messages.pop(first_non_system)
            total = self.calc_tokens()

    def calc_tokens(self):
        return num_tokens_from_messages(self.messages, self.model)

    def _calc_last_message_tokens(self) -> int:
        if not self.messages:
            return 0
        return estimate_message_tokens(self.messages[-1])


# refer to https://github.com/openai/openai-cookbook/blob/main/examples/How_to_count_tokens_with_tiktoken.ipynb
def num_tokens_from_messages(messages, model):
    """Returns the number of tokens used by a list of messages."""

    if model in ["wenxin", "xunfei", const.GEMINI] or model in const.GPT54_LIST:
        return sum(estimate_message_tokens(msg) for msg in messages)

    import tiktoken

    if model in ["gpt-3.5-turbo-0301", "gpt-35-turbo", "gpt-3.5-turbo-1106"]:
        return num_tokens_from_messages(messages, model="gpt-3.5-turbo")
    elif model in ["gpt-4-0314", "gpt-4-0613", "gpt-4-32k", "gpt-4-32k-0613", "gpt-3.5-turbo-0613",
                   "gpt-3.5-turbo-16k", "gpt-3.5-turbo-16k-0613", "gpt-35-turbo-16k", 
                   const.GPT4_TURBO_PREVIEW, const.GPT4_VISION_PREVIEW, const.GPT4_TURBO,const.GPT4_OMNI]:
        return num_tokens_from_messages(messages, model="gpt-4")

    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        logger.debug("Warning: model not found. Using cl100k_base encoding.")
        encoding = tiktoken.get_encoding("cl100k_base")
    if model == "gpt-3.5-turbo":
        tokens_per_message = 4  # every message follows <|start|>{role/name}\n{content}<|end|>\n
        tokens_per_name = -1  # if there's a name, the role is omitted
    elif model == "gpt-4":
        tokens_per_message = 3
        tokens_per_name = 1
    else:
        logger.warning(f"num_tokens_from_messages() is not implemented for model {model}. Returning num tokens assuming gpt-3.5-turbo.")
        return num_tokens_from_messages(messages, model="gpt-3.5-turbo")
    num_tokens = 0
    for message in messages:
        num_tokens += tokens_per_message
        num_tokens += _count_message_tokens_with_encoding(message, encoding, tokens_per_name)
    num_tokens += 3  # every reply is primed with <|start|>assistant<|message|>
    return num_tokens

def estimate_message_tokens(message: dict) -> int:
    total = 4
    content = message.get("content", "")

    if isinstance(content, str):
        total += len(content) // 2
    elif isinstance(content, list):
        for block in content:
            total += _estimate_content_block_tokens(block)
    elif isinstance(content, dict):
        total += _estimate_content_block_tokens(content)
    else:
        total += len(str(content)) // 2

    name = message.get("name")
    if name:
        total += len(str(name)) // 2
    return total


def _estimate_content_block_tokens(block) -> int:
    if not isinstance(block, dict):
        return len(str(block)) // 2

    block_type = block.get("type")
    if block_type in {"text", "input_text", "output_text"}:
        return len(block.get("text", "")) // 2
    if block_type in {"image_url", "input_image", "image"}:
        return 1500
    if block_type in {"video_url", "input_video", "video"}:
        return 8000
    if block_type == "document":
        source = block.get("source", {})
        return len(source.get("data", "")) // 4

    total = 0
    for value in block.values():
        if isinstance(value, str):
            total += len(value) // 2
        elif isinstance(value, dict):
            total += _estimate_content_block_tokens(value)
    return total


def _count_message_tokens_with_encoding(message: dict, encoding, tokens_per_name: int) -> int:
    total = 0
    for key, value in message.items():
        if key == "content":
            total += _count_content_tokens_with_encoding(value, encoding)
        else:
            total += len(encoding.encode(str(value)))
            if key == "name":
                total += tokens_per_name
    return total


def _count_content_tokens_with_encoding(content, encoding) -> int:
    if isinstance(content, str):
        return len(encoding.encode(content))
    if isinstance(content, list):
        return sum(_count_content_tokens_with_encoding(block, encoding) for block in content)
    if isinstance(content, dict):
        return _count_content_dict_tokens_with_encoding(content, encoding)
    return len(encoding.encode(str(content)))


def _count_content_dict_tokens_with_encoding(content: dict, encoding) -> int:
    block_type = content.get("type")
    if block_type in {"text", "input_text", "output_text"}:
        return len(encoding.encode(content.get("text", "")))
    if block_type in {"image_url", "input_image", "image"}:
        image_url = content.get("image_url")
        if isinstance(image_url, dict):
            image_url = image_url.get("url", "")
        return len(encoding.encode(str(image_url))) if image_url else 1500
    if block_type in {"video_url", "input_video", "video"}:
        video_url = content.get("video_url")
        if isinstance(video_url, dict):
            url = video_url.get("url", "")
            remote_url = video_url.get("remote_url", "")
            payload = f"{url}{remote_url}"
            return len(encoding.encode(payload)) if payload else 8000
        return len(encoding.encode(str(video_url))) if video_url else 8000

    total = 0
    for value in content.values():
        if isinstance(value, str):
            total += len(encoding.encode(value))
        elif isinstance(value, list):
            total += sum(_count_content_tokens_with_encoding(item, encoding) for item in value)
        elif isinstance(value, dict):
            total += _count_content_dict_tokens_with_encoding(value, encoding)
    return total
