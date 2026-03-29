import time

from volcenginesdkarkruntime import Ark

from bot.bot import Bot
from bot.ark.ark_media import process_image_files, size_calculator, size_calculator_from_data_urls
from bridge.context import Context
from bridge.reply import Reply, ReplyType
from common import const, memory
from common.log import logger
from common.model_status import model_state
from common.utils import get_chat_session_manager, get_image_urls_from_session, url_to_base64
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
            session_manager = get_chat_session_manager(session_id)
            logger.info(f"[{model.upper()}] query={query}, requester={session_id}")
            session_manager.session_query(query, session_id)

            file_cache = memory.USER_IMAGE_CACHE.get(session_id)
            session_images = []
            duration_seconds = video_state.get_video_duration(session_id)
            resolution = video_state.get_video_resolution(session_id)
            content = [{
                "type": "text",
                "text": query
            }]
            ratio = conf().get("image_aspect_ratio", "16:9")
            request_resolution = "480p"

            if file_cache:
                ratio = size_calculator(file_cache["files"])
                request_resolution = resolution
                content.extend(process_image_files(file_cache))
                memory.USER_IMAGE_CACHE.pop(session_id)
            else:
                session_images = get_image_urls_from_session(session_id, session_manager)
                if session_images:
                    ratio = size_calculator_from_data_urls(session_images)
                    request_resolution = resolution
                    content.extend([
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": image_url
                            }
                        }
                        for image_url in session_images
                    ])
                    logger.info(f"[{model.upper()}] 从 session 历史取参考图, count={len(session_images)}")

            response = self.client.content_generation.tasks.create(
                **self._build_task_params(
                    model=model,
                    content=content,
                    resolution=request_resolution,
                    ratio=self._normalize_ratio_for_model(model, ratio),
                    duration_seconds=duration_seconds
                )
            )
            video_duration, video_url = self.get_video_info(response.id, model)

            try:
                base64_data = url_to_base64(video_url)
                session_manager.session_inject_media(
                    session_id=session_id,
                    media_type="video",
                    data=base64_data,
                    source_model=model,
                    remote_url=video_url
                )
                logger.info(f"[{model.upper()}] video injected to session, model={model}, session_id={session_id}")
            except Exception as e:
                logger.warning(f"[{model.upper()}] failed to inject video to session: {e}")

            return Reply(ReplyType.VIDEO_URL, (video_duration, video_url))
        except Exception as e:
            logger.error(f"[{model.upper()}] fetch reply error: {e}")
            return Reply(ReplyType.ERROR, self._format_error_message(model, e))

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

    def _build_task_params(self, *, model, content, resolution, ratio, duration_seconds):
        params = {
            "model": model,
            "content": content,
            "resolution": resolution,
            "ratio": ratio,
            "duration": duration_seconds,
            "camera_fixed": False,
            "watermark": True,
        }

        if model == const.DOUBAO_SEEDANCE_15_PRO:
            params["generate_audio"] = conf().get("video_sound", "off") == "on"

        return params

    def _normalize_ratio_for_model(self, model, ratio):
        if model not in const.DOUBAO_SEEDANCE_LIST:
            return ratio

        allowed_ratios = {
            "16:9": 16 / 9,
            "4:3": 4 / 3,
            "1:1": 1.0,
            "3:4": 3 / 4,
            "9:16": 9 / 16,
            "21:9": 21 / 9,
            "adaptive": -1,
        }
        if ratio in allowed_ratios:
            return ratio

        ratio_value = self._ratio_to_float(ratio)
        if ratio_value is None:
            logger.warning(f"[{model.upper()}] unsupported ratio={ratio}, fallback to 16:9")
            return "16:9"

        candidate_ratios = {key: value for key, value in allowed_ratios.items() if key != "adaptive"}
        normalized_ratio = min(candidate_ratios, key=lambda key: abs(candidate_ratios[key] - ratio_value))
        logger.info(f"[{model.upper()}] ratio {ratio} 不在白名单内，已映射为 {normalized_ratio}")
        return normalized_ratio

    def _ratio_to_float(self, ratio):
        try:
            width, height = str(ratio).split(":", 1)
            return float(width) / float(height)
        except (TypeError, ValueError, ZeroDivisionError):
            return None

    def _format_error_message(self, model, error):
        error_text = str(error)
        if self._is_seedance_duration_error(model, error_text):
            return (
                "Seedance 当前不支持这个视频时长。"
                "请先把视频长度设置为 4 到 12 秒之间的整数，再重新生成。"
            )
        return f"[{model.upper()}] {error_text}"

    def _is_seedance_duration_error(self, model, error_text):
        if model != const.DOUBAO_SEEDANCE_15_PRO:
            return False
        lowered = error_text.lower()
        if "duration" not in lowered:
            return False
        return (
            "not supported" in lowered
            or "not valid" in lowered
            or "invalidparameter" in lowered
        )
