from volcenginesdkarkruntime import Ark

from bot.bot import Bot
from bot.ark.ark_media import (
    build_seedream_size,
    encode_image,
    get_image_from_session,
    size_calculator,
    size_calculator_from_data_urls,
)
from bridge.context import Context
from bridge.reply import Reply, ReplyType
from common import const
from common import memory
from common.log import logger
from common.model_status import model_state
from common.utils import get_ark_sessions, url_to_base64
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
            logger.info(f"[{model.upper()}] query={query}, requester={session_id}")
            get_ark_sessions().session_query(query, session_id)

            params = {
                "model": model,
                "prompt": query,
                "response_format": "url",
                "watermark": True,
                "sequential_image_generation": "disabled"
            }
            default_aspect_ratio = conf().get("image_aspect_ratio", "16:9")

            file_cache = memory.USER_IMAGE_CACHE.get(session_id)
            session_images = []
            if file_cache:
                images = [
                    encode_image(path, file)
                    for path, file in zip(file_cache["path"], file_cache["files"])
                ]
                aspect_ratio = size_calculator(file_cache["files"])
                params.update({
                    "image": images,
                    "size": self._build_image_size(model, aspect_ratio)
                })
                memory.USER_IMAGE_CACHE.pop(session_id)
            else:
                session_images = get_image_from_session(session_id)
                if session_images:
                    aspect_ratio = size_calculator_from_data_urls(session_images)
                    params.update({
                        "image": session_images,
                        "size": self._build_image_size(model, aspect_ratio)
                    })
                    logger.info(f"[DoubaoImage] 从 session 历史取参考图, count={len(session_images)}")
                else:
                    params["size"] = self._build_image_size(model, default_aspect_ratio)

            response = self.client.images.generate(**params)
            image_url = response.data[0].url

            try:
                base64_data = url_to_base64(image_url)
                get_ark_sessions().session_inject_media(
                    session_id=session_id,
                    media_type="image",
                    data=base64_data,
                    source_model=model
                )
                logger.info(f"[DoubaoImage] image injected to session, model={model}, session_id={session_id}")
            except Exception as e:
                logger.warning(f"[DoubaoImage] failed to inject image to session: {e}")

            return Reply(ReplyType.IMAGE_URL, image_url)
        except Exception as e:
            logger.error(f"[DoubaoImage] fetch reply error: {e}")
            return Reply(ReplyType.ERROR, f"[DoubaoImage] {e}")

    def _build_image_size(self, model, aspect_ratio):
        if model in const.DOUBAO_SEEDREAM_LIST:
            return build_seedream_size(model, self.image_size, aspect_ratio)
        return self.image_size
