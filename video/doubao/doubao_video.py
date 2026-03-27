import time

from volcenginesdkarkruntime import Ark

from bot.bot import Bot
from bot.ark.ark_media import get_image_from_session, process_image_files
from bridge.context import Context
from bridge.reply import Reply, ReplyType
from common import memory
from common.log import logger
from common.model_status import model_state
from common.utils import get_ark_sessions, url_to_base64
from common.video_status import video_state
from config import conf


class DoubaoVideoBot(Bot):
    def __init__(self):
        super().__init__()
        self.client = Ark(api_key=conf().get("ark_api_key"))

    def reply(self, query, context: Context = None) -> Reply:
        try:
            session_id = context["session_id"]
            model = model_state.get_video_state(session_id)
            logger.info(f"[{model.upper()}] query={query}, requester={session_id}")
            get_ark_sessions().session_query(query, session_id)

            file_cache = memory.USER_IMAGE_CACHE.get(session_id)
            session_images = []
            duration_seconds = video_state.get_video_duration(session_id)
            resolution = video_state.get_video_resolution(session_id)
            content = [{
                "type": "text",
                "text": f"{query} --resolution {resolution if file_cache else '480p'} --duration {duration_seconds} --camerafixed false --watermark true"
            }]

            if file_cache:
                content.extend(process_image_files(file_cache))
                memory.USER_IMAGE_CACHE.pop(session_id)
            else:
                session_images = get_image_from_session(session_id)
                if session_images:
                    content[0]["text"] = f"{query} --resolution {resolution} --duration {duration_seconds} --camerafixed false --watermark true"
                    content.extend([
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": image_url
                            }
                        }
                        for image_url in session_images
                    ])
                    logger.info(f"[DoubaoVideo] 从 session 历史取参考图, count={len(session_images)}")

            response = self.client.content_generation.tasks.create(
                model=model,
                content=content
            )
            video_duration, video_url = self.get_video_info(response.id, model)

            try:
                base64_data = url_to_base64(video_url)
                get_ark_sessions().session_inject_media(
                    session_id=session_id,
                    media_type="video",
                    data=base64_data,
                    source_model=model
                )
                logger.info(f"[DoubaoVideo] video injected to session, model={model}, session_id={session_id}")
            except Exception as e:
                logger.warning(f"[DoubaoVideo] failed to inject video to session: {e}")

            return Reply(ReplyType.VIDEO_URL, (video_duration, video_url))
        except Exception as e:
            logger.error(f"[DoubaoVideo] fetch reply error: {e}")
            return Reply(ReplyType.ERROR, f"[DoubaoVideo] {e}")

    def get_video_info(self, task_id, model):
        logger.info(f"[{model.upper()}] polling task status, task_id={task_id}")
        while True:
            get_result = self.client.content_generation.tasks.get(task_id=task_id)
            status = get_result.status
            if status == "succeeded":
                logger.info(f"[{model.upper()}] task succeeded, task_id={task_id}")
                return get_result.duration, get_result.content.video_url
            if status == "failed":
                logger.error(f"[{model.upper()}] task failed, task_id={task_id}, error={get_result.error}")
                raise RuntimeError(get_result.error)
            logger.info(f"[{model.upper()}] current status={status}, task_id={task_id}, retry after 3 seconds")
            time.sleep(3)
