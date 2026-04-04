import time

from volcenginesdkarkruntime import Ark

from bot.bot import Bot
from bot.ark.ark_media import process_image_files, size_calculator, size_calculator_from_data_urls, aspect_ratio_from_size
from bridge.context import Context
from bridge.reply import Reply, ReplyType
from common.aspect_ratio import parse_aspect_ratio_from_prompt
from common import const, memory
from common.log import logger
from common.model_status import model_state
from common.utils import get_chat_session_manager, get_image_urls_from_session, infer_aspect_ratio_from_video_cache, url_to_base64
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
            quoted_cache = memory.USER_QUOTED_IMAGE_CACHE.get(session_id)
            quoted_video_cache = memory.USER_QUOTED_VIDEO_CACHE.get(session_id)
            video_cache = memory.USER_VIDEO_CACHE.get(session_id)
            has_video_reference_source = bool(quoted_video_cache or video_cache)
            duration_seconds = self._normalize_duration_for_model(model, video_state.get_video_duration(session_id))
            resolution = self._normalize_resolution_for_model(model, video_state.get_video_resolution(session_id), has_reference=False)
            content = [{
                "type": "text",
                "text": query
            }]
            prompt_ratio = self._parse_aspect_ratio_from_prompt(query)
            if prompt_ratio:
                logger.info(f"[{model.upper()}] 从 prompt 中解析到比例: {prompt_ratio}")
            ratio = prompt_ratio or conf().get("image_aspect_ratio", "16:9")
            request_resolution = "480p"
            reference_image_count = 0
            reference_video_count = 0
            selected_video_cache = None

            if quoted_cache:
                if not has_video_reference_source:
                    ratio = prompt_ratio or size_calculator(quoted_cache["files"])
                request_resolution = self._normalize_resolution_for_model(model, resolution, has_reference=True)
                image_contents = process_image_files(quoted_cache)
                reference_image_count = len(image_contents)
                content.extend(image_contents)
                logger.info(f"[{model.upper()}] 从回复引用图取参考图, count={len(quoted_cache['files'])}")
                memory.USER_QUOTED_IMAGE_CACHE.pop(session_id)
            elif file_cache:
                if not has_video_reference_source:
                    ratio = prompt_ratio or size_calculator(file_cache["files"])
                request_resolution = resolution
                image_contents = process_image_files(file_cache)
                reference_image_count = len(image_contents)
                content.extend(image_contents)
                logger.info(f"[{model.upper()}] 从内存参考图取参考图, count={len(file_cache['files'])}")
                memory.USER_IMAGE_CACHE.pop(session_id)
            else:
                session_images = get_image_urls_from_session(session_id, session_manager)
                if session_images:
                    if not has_video_reference_source:
                        ratio = prompt_ratio or size_calculator_from_data_urls(session_images)
                    request_resolution = self._normalize_resolution_for_model(model, resolution, has_reference=True)
                    reference_image_count = len(session_images)
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

            if quoted_video_cache:
                selected_video_cache = quoted_video_cache
                reference_videos = self._build_reference_videos(quoted_video_cache, model)
                if reference_videos:
                    reference_video_count = len(reference_videos)
                    content.extend(reference_videos)
                    logger.info(f"[{model.upper()}] 从回复引用视频取参考视频, count={len(reference_videos)}")
                memory.USER_QUOTED_VIDEO_CACHE.pop(session_id)
            elif video_cache:
                selected_video_cache = video_cache
                reference_videos = self._build_reference_videos(video_cache, model)
                if reference_videos:
                    reference_video_count = len(reference_videos)
                    content.extend(reference_videos)
                    logger.info(f"[{model.upper()}] 从内存参考视频取参考视频, count={len(reference_videos)}")
                memory.USER_VIDEO_CACHE.pop(session_id)

            if reference_video_count and selected_video_cache:
                video_ratio = self._infer_aspect_ratio_from_video_cache(selected_video_cache, model)
                if video_ratio:
                    ratio = video_ratio
                    logger.info(f"[{model.upper()}] 从参考视频推断比例: {video_ratio}")

            if reference_image_count and reference_video_count:
                self._mark_images_as_reference(content)
                logger.info(f"[{model.upper()}] 图视频混合参考模式: 已将图片内容标记为 reference_image")

            final_resolution = self._normalize_resolution_for_model(model, request_resolution, has_reference=bool(content[1:]))
            final_ratio = self._normalize_ratio_for_model(model, ratio)
            logger.info(
                f"[{model.upper()}] 参考素材统计: reference_images={reference_image_count}, "
                f"reference_videos={reference_video_count}"
            )
            logger.info(
                f"[{model.upper()}] 请求参数: resolution={final_resolution}, "
                f"ratio={final_ratio}, duration={duration_seconds}"
            )

            response = self.client.content_generation.tasks.create(
                **self._build_task_params(
                    model=model,
                    content=content,
                    resolution=final_resolution,
                    ratio=final_ratio,
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

    def _build_reference_videos(self, video_cache, model):
        reference_videos = []
        for video_file in video_cache.get("files", []):
            public_url = video_file.get("public_url")
            if not public_url:
                logger.warning(f"[{model.upper()}] reference video missing public_url, skipped")
                continue
            logger.info(f"[{model.upper()}] reference video public_url={public_url}")
            reference_videos.append({
                "type": "video_url",
                "video_url": {
                    "url": public_url
                },
                "role": "reference_video",
            })
        return reference_videos[:3]

    def _mark_images_as_reference(self, content):
        for item in content:
            if item.get("type") == "image_url":
                item["role"] = "reference_image"

    def _infer_aspect_ratio_from_video_cache(self, video_cache, model):
        ratio = infer_aspect_ratio_from_video_cache(
            video_cache,
            lambda video_size: self._normalize_ratio_for_model(model, aspect_ratio_from_size(video_size))
        )
        if ratio is None:
            logger.warning(f"[{model.upper()}] failed to infer aspect ratio from reference video cache")
        return ratio

    def _parse_aspect_ratio_from_prompt(self, prompt):
        return parse_aspect_ratio_from_prompt(prompt)

    def _normalize_ratio_for_model(self, model, ratio):
        if model not in const.DOUBAO_SEEDANCE_LIST:
            return ratio

        allowed_ratios = self._get_allowed_ratios_for_model(model)
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

    def _normalize_duration_for_model(self, model, duration):
        if model not in const.DOUBAO_SEEDANCE_LIST:
            return duration
        try:
            normalized_duration = int(duration)
        except (TypeError, ValueError):
            fallback_duration = self._get_default_duration_for_model(model)
            logger.warning(f"[{model.upper()}] invalid duration={duration}, fallback to {fallback_duration}")
            return fallback_duration
        minimum_duration, maximum_duration = self._get_duration_range_for_model(model)
        if minimum_duration <= normalized_duration <= maximum_duration:
            return normalized_duration
        fallback_duration = self._get_default_duration_for_model(model)
        logger.warning(f"[{model.upper()}] invalid duration={duration}, fallback to {fallback_duration}")
        return fallback_duration

    def _normalize_resolution_for_model(self, model, resolution, has_reference):
        if model not in const.DOUBAO_SEEDANCE_LIST:
            return resolution
        normalized_resolution = str(resolution).strip().lower()
        allowed_resolutions = self._get_allowed_resolutions_for_model(model)
        if normalized_resolution not in allowed_resolutions:
            fallback_resolution = self._get_default_resolution_for_model(model)
            logger.warning(f"[{model.upper()}] invalid resolution={resolution}, fallback to {fallback_resolution}")
            normalized_resolution = fallback_resolution
        if has_reference and normalized_resolution == "1080p":
            logger.warning(f"[{model.upper()}] resolution=1080p is not supported with reference images, fallback to 720p")
            return "720p"
        return normalized_resolution

    def _get_allowed_ratios_for_model(self, model):
        if model in {const.DOUBAO_SEEDANCE_20, const.DOUBAO_SEEDANCE_20_FAST}:
            return {
                "21:9": 21 / 9,
                "16:9": 16 / 9,
                "4:3": 4 / 3,
                "1:1": 1.0,
                "3:4": 3 / 4,
                "9:16": 9 / 16,
            }
        return {
            "16:9": 16 / 9,
            "4:3": 4 / 3,
            "1:1": 1.0,
            "3:4": 3 / 4,
            "9:16": 9 / 16,
            "21:9": 21 / 9,
            "adaptive": -1,
        }

    def _get_duration_range_for_model(self, model):
        if model in {const.DOUBAO_SEEDANCE_20, const.DOUBAO_SEEDANCE_20_FAST}:
            return 4, 15
        return 2, 12

    def _get_default_duration_for_model(self, model):
        if model in {const.DOUBAO_SEEDANCE_20, const.DOUBAO_SEEDANCE_20_FAST}:
            return 5
        return 5

    def _get_allowed_resolutions_for_model(self, model):
        if model in {const.DOUBAO_SEEDANCE_20, const.DOUBAO_SEEDANCE_20_FAST}:
            return {"480p", "720p"}
        return {"480p", "720p", "1080p"}

    def _get_default_resolution_for_model(self, model):
        if model in {const.DOUBAO_SEEDANCE_20, const.DOUBAO_SEEDANCE_20_FAST}:
            return "720p"
        return "720p"

    def _ratio_to_float(self, ratio):
        try:
            width, height = str(ratio).split(":", 1)
            return float(width) / float(height)
        except (TypeError, ValueError, ZeroDivisionError):
            return None

    def _format_error_message(self, model, error):
        error_text = str(error)
        if self._is_reference_video_fetch_timeout_error(error_text):
            return (
                "参考视频拉取超时了。"
                "通常是因为视频码流过高、文件过大，或公网地址下载速度太慢。"
                "建议先压低码流、减小体积后再试。"
            )
        if self._is_reference_video_too_short_error(model, error_text):
            return (
                "参考视频时长不符合官方要求。"
                "请使用单个视频时长在 2 到 15 秒之间的参考视频后再试。"
            )
        if self._is_seedance_duration_error(model, error_text):
            return (
                "Seedance 当前不支持这个视频时长。"
                "请先把视频长度设置为该模型支持的整数范围后，再重新生成。"
            )
        return f"[{model.upper()}] {error_text}"

    def _is_reference_video_fetch_timeout_error(self, error_text):
        lowered = error_text.lower()
        return "video_url" in lowered and "timeout while fetching resource" in lowered

    def _is_reference_video_too_short_error(self, model, error_text):
        if model not in const.DOUBAO_SEEDANCE_LIST:
            return False
        lowered = error_text.lower()
        return (
            "content[" in lowered
            and "video duration" in lowered
            and "greater than or equal to" in lowered
            and "r2v" in lowered
        )

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
