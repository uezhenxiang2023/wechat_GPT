from volcenginesdkarkruntime import Ark

from bot.bot import Bot
from bot.ark.ark_media import (
    build_seedream_size,
    encode_image,
    size_calculator,
)
from bridge.context import Context
from bridge.reply import Reply, ReplyType
from common import const
from common import memory
from common.aspect_ratio import parse_aspect_ratio_from_prompt
from common.log import logger
from common.model_status import model_state
from common.utils import get_chat_session_manager, url_to_base64
from config import conf


class DoubaoImageBot(Bot):
    def __init__(self):
        super().__init__()
        self.client = Ark(api_key=conf().get("ark_api_key"))
        self.image_size = conf().get("image_create_size")

    def reply(self, query, context: Context = None) -> Reply:
        try:
            session_id = context["session_id"]
            model = model_state.get_image_model(session_id)
            session_manager = get_chat_session_manager(session_id)
            logger.info(f"[{model.upper()}] query={query}, requester={session_id}")
            session_manager.session_query(query, session_id)

            params = {
                "model": model,
                "prompt": query,
                "response_format": "url",
                "watermark": True,
                "sequential_image_generation": "disabled"
            }
            default_aspect_ratio = conf().get("image_aspect_ratio", "16:9")
            prompt_aspect_ratio = self._parse_aspect_ratio_from_prompt(query)
            if prompt_aspect_ratio:
                logger.info(f"[{model.upper()}] 从 prompt 中解析到比例: {prompt_aspect_ratio}")

            quoted_cache = memory.USER_QUOTED_IMAGE_CACHE.get(session_id)
            if quoted_cache:
                images = [
                    encode_image(path, file)
                    for path, file in zip(quoted_cache["path"], quoted_cache["files"])
                ]
                aspect_ratio = prompt_aspect_ratio or size_calculator(quoted_cache["files"])
                image_size = self._build_image_size(model, aspect_ratio)
                params.update({
                    "image": images,
                    "size": image_size
                })
                if not prompt_aspect_ratio:
                    logger.info(
                        f"[{model.upper()}] 从回复引用图取参考图推断比例: {aspect_ratio}, "
                        f"size={image_size}, count={len(images)}"
                    )
                memory.USER_QUOTED_IMAGE_CACHE.pop(session_id)
            else:
                file_cache = memory.USER_IMAGE_CACHE.get(session_id)
                if file_cache:
                    images = [
                        encode_image(path, file)
                        for path, file in zip(file_cache["path"], file_cache["files"])
                    ]
                    aspect_ratio = prompt_aspect_ratio or size_calculator(file_cache["files"])
                    image_size = self._build_image_size(model, aspect_ratio)
                    params.update({
                        "image": images,
                        "size": image_size
                    })
                    if not prompt_aspect_ratio:
                        logger.info(
                            f"[{model.upper()}] 从内存参考图推断比例: {aspect_ratio}, "
                            f"size={image_size}, count={len(images)}"
                        )
                    memory.USER_IMAGE_CACHE.pop(session_id)
                else:
                    aspect_ratio = prompt_aspect_ratio or default_aspect_ratio
                    image_size = self._build_image_size(model, aspect_ratio)
                    params["size"] = image_size
                    logger.info(
                        f"[{model.upper()}] 当前为文生图模式, aspect_ratio={aspect_ratio}, image_size={image_size}"
                    )

            response = self.client.images.generate(**params)
            image_url = response.data[0].url

            try:
                base64_data = url_to_base64(image_url)
                session_manager.session_inject_media(
                    session_id=session_id,
                    media_type="image",
                    data=base64_data,
                    source_model=model
                )
                logger.info(f"[{model.upper()}] image injected to session, model={model}, session_id={session_id}")
            except Exception as e:
                logger.warning(f"[{model.upper()}] failed to inject image to session: {e}")

            return Reply(ReplyType.IMAGE_URL, image_url)
        except Exception as e:
            logger.error(f"[{model.upper()}] fetch reply error: {e}")
            return Reply(ReplyType.ERROR, f"[{model.upper()}]s {e}")

    def _build_image_size(self, model, aspect_ratio):
        if model in const.DOUBAO_SEEDREAM_LIST:
            return build_seedream_size(model, self.image_size, aspect_ratio)
        return self.image_size

    def _parse_aspect_ratio_from_prompt(self, prompt):
        return parse_aspect_ratio_from_prompt(prompt)
