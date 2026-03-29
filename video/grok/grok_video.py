import datetime

from xai_sdk import Client

from bot.bot import Bot
from bridge.context import Context
from bridge.reply import Reply, ReplyType
from common import const, memory
from common.log import logger
from common.model_status import model_state
from common.utils import get_chat_session_manager, get_image_urls_from_session, get_video_urls_from_session, url_to_base64
from common.video_status import video_state
from config import conf


_GROK_VIDEO_RATIO_MAP = {
    "1:1": 1.0,
    "16:9": 16 / 9,
    "9:16": 9 / 16,
    "4:3": 4 / 3,
    "3:4": 3 / 4,
    "3:2": 3 / 2,
    "2:3": 2 / 3,
}


class GrokVideoBot(Bot):
    def __init__(self):
        super().__init__()
        self.client = Client(
            api_key=conf().get("grok_api_key"),
            timeout=conf().get("request_timeout", 180)
        )

    def reply(self, query, context: Context = None) -> Reply:
        try:
            session_id = context["session_id"]
            model = model_state.get_video_state(session_id)
            session_manager = get_chat_session_manager(session_id)
            logger.info(f"[{model.upper()}] query={query}, requester={session_id}")
            session_manager.session_query(query, session_id)

            request_args = self._build_video_args(session_id, model)
            if request_args.get("video_url"):
                logger.info(f"[{model.upper()}] 使用视频编辑模式")
            response = self.client.video.generate(
                prompt=query,
                model=model,
                timeout=datetime.timedelta(seconds=conf().get("request_timeout", 180)),
                interval=datetime.timedelta(seconds=3),
                **request_args
            )

            video_url = response.url
            video_duration = response.duration

            try:
                base64_data = url_to_base64(video_url)
                session_manager.session_inject_media(
                    session_id=session_id,
                    media_type="video",
                    data=base64_data,
                    source_model=model,
                    mime_type="video/mp4",
                    remote_url=video_url
                )
                logger.info(f"[{model.upper()}] video injected to session, model={model}, session_id={session_id}")
            except Exception as e:
                logger.warning(f"[{model.upper()}] failed to inject video to session: {e}")

            return Reply(ReplyType.VIDEO_URL, (video_duration, video_url))
        except Exception as e:
            logger.error(f"[{model.upper()}] fetch reply error: {e}")
            return Reply(ReplyType.ERROR, f"[{model.upper()}] {e}")

    def _build_video_args(self, session_id, model):
        video_cache = memory.USER_VIDEO_CACHE.get(session_id)
        if video_cache:
            memory.USER_VIDEO_CACHE.pop(session_id)
            cached_videos = video_cache.get("files", [])
            if cached_videos:
                public_url = cached_videos[-1].get("public_url")
                if public_url:
                    logger.info(f"[{model.upper()}] 从内存视频缓存取参考视频")
                    return {"video_url": public_url}
                logger.warning(f"[{model.upper()}] 检测到上传视频，但未配置可访问的媒体公网地址")
                raise ValueError("当前上传视频还没有可访问的公网 URL。请先在配置中设置 media_public_base_url，再重试 Grok 视频编辑。")

        session_videos = get_video_urls_from_session(session_id)
        if session_videos:
            logger.info(f"[{model.upper()}] 从 session 历史取参考视频, count={len(session_videos)}")
            return {"video_url": session_videos[0]}

        file_cache = memory.USER_IMAGE_CACHE.get(session_id)
        image_urls = []
        if file_cache:
            image_urls = [
                self._encode_pil_image(image_file, model)
                for image_file in file_cache.get("files", [])
            ]
            image_urls = [image_url for image_url in image_urls if image_url]
            memory.USER_IMAGE_CACHE.pop(session_id)
            if image_urls:
                aspect_ratio = self._infer_aspect_ratio_from_data_urls(image_urls, model)
                logger.info(f"[{model.upper()}] 从内存参考图推断比例: {aspect_ratio}, count={len(image_urls)}")
                return {"image_url": image_urls[0]}

        session_images = get_image_urls_from_session(session_id)
        if session_images:
            aspect_ratio = self._infer_aspect_ratio_from_data_urls(session_images, model)
            logger.info(f"[{model.upper()}] 从 session 历史取参考图, count={len(session_images)}")
            logger.info(f"[{model.upper()}] 从 session 历史参考图推断比例: {aspect_ratio}")
            return {"image_url": session_images[0]}

        return {
            "duration": self._normalize_duration(video_state.get_video_duration(session_id)),
            "aspect_ratio": self._normalize_aspect_ratio(conf().get("image_aspect_ratio", "16:9"), model),
            "resolution": self._normalize_resolution(video_state.get_video_resolution(session_id), model),
        }

    def _encode_pil_image(self, image, model):
        import base64
        import io

        try:
            if image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGB")
            fmt = "PNG" if image.format == "PNG" else "JPEG"
            mime_type = "image/png" if fmt == "PNG" else "image/jpeg"
            buf = io.BytesIO()
            image.save(buf, format=fmt)
            data = base64.b64encode(buf.getvalue()).decode("utf-8")
            return f"data:{mime_type};base64,{data}"
        except Exception as e:
            logger.warning(f"[{model.upper()}] failed to encode cached image: {e}")
            return None

    def _normalize_duration(self, duration):
        value = int(duration or 5)
        if value < 5:
            return 5
        if value > 15:
            return 15
        return value

    def _normalize_resolution(self, resolution, model):
        normalized = str(resolution).strip().lower()
        if normalized in {"480p", "720p"}:
            return normalized
        logger.warning(f"[{model.upper()}] invalid resolution={resolution}, fallback to 720p")
        return "720p"

    def _normalize_aspect_ratio(self, aspect_ratio, model):
        normalized = str(aspect_ratio).strip()
        if normalized in _GROK_VIDEO_RATIO_MAP:
            return normalized
        logger.warning(f"[{model.upper()}] invalid aspect_ratio={aspect_ratio}, fallback to 16:9")
        return "16:9"

    def _infer_aspect_ratio_from_data_urls(self, image_urls, model):
        import base64
        import io
        from PIL import Image

        sizes = []
        for image_url in image_urls:
            try:
                _, b64_data = image_url.split(",", 1)
                image = io.BytesIO(base64.b64decode(b64_data))
                with Image.open(image) as pil_image:
                    sizes.append(pil_image.size)
            except Exception as e:
                logger.warning(f"[{model.upper()}] failed to infer aspect ratio from image: {e}")
        if not sizes:
            return self._normalize_aspect_ratio(conf().get("image_aspect_ratio", "16:9"), model)
        best_size = sorted(sizes, key=lambda size: size[0] * size[1], reverse=True)[0]
        ratio = round(best_size[0] / best_size[1], 4)
        return min(_GROK_VIDEO_RATIO_MAP, key=lambda key: abs(_GROK_VIDEO_RATIO_MAP[key] - ratio))
