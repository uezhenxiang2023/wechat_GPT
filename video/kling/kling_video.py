# video/kling/kling_video.py

import io
import time
import jwt
import requests
import base64

from PIL import Image

from bot.bot import Bot
from bridge.context import Context
from bridge.reply import Reply, ReplyType
from common.aspect_ratio import parse_aspect_ratio_from_prompt
from common.log import logger
from common.utils import get_chat_session_manager, get_image_urls_from_session, url_to_base64
from common import const, memory
from config import conf
from common.model_status import model_state
from common.video_status import video_state


class KlingVideoBot(Bot):

    API_BASE = "https://api-beijing.klingai.com"
    ENDPOINT_OMNI = "/v1/videos/omni-video"
    _ALLOWED_VIDEO_RATIOS = {"16:9", "9:16", "1:1"}
    _ALLOWED_VIDEO_DURATIONS = {str(value) for value in range(3, 16)}

    def __init__(self):
        super().__init__()
        self.access_key = conf().get("kling_access_key")
        self.secret_key = conf().get("kling_secret_key")

    def _get_token(self) -> str:
        headers = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "iss": self.access_key,
            "exp": int(time.time()) + 1800,
            "nbf": int(time.time()) - 5
        }
        return jwt.encode(payload, self.secret_key, headers=headers)

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._get_token()}"
        }

    def reply(self, query, context: Context = None) -> Reply:
        try:
            session_id = context["session_id"]
            model = model_state.get_video_state(session_id) or const.KLING_VIDEO_O1
            duration = self._normalize_duration(video_state.get_video_duration(session_id), model)
            resolution = self._normalize_resolution(video_state.get_video_resolution(session_id), model)
            mode = self._mode_from_resolution(resolution)
            session_manager = get_chat_session_manager(session_id)

            logger.info(f"[{model.upper()}] query={query}, requester={session_id}")
            session_manager.session_query(query, session_id)

            payload = {
                "model_name": model,
                "prompt": query,
                "mode": mode,
                "duration": duration,
                "watermark_info": {"enabled": True},
                "sound": conf().get("video_sound", "off")
            }

            # 用户 prompt 中的比例优先级最高
            prompt_ratio = self._parse_aspect_ratio_from_prompt(query)
            if prompt_ratio:
                payload["aspect_ratio"] = prompt_ratio
                logger.info(f"[{model.upper()}] 从 prompt 中解析到比例: {prompt_ratio}")

            # 参考图捕获：引用缓存——>内存缓存——>session 历史
            file_cache = memory.USER_QUOTED_IMAGE_CACHE.get(session_id)
            session_images = []
            reference_image_count = 0
            if not file_cache:
                file_cache = memory.USER_IMAGE_CACHE.get(session_id)
            if not file_cache:
                session_images = get_image_urls_from_session(session_id, session_manager)

            if file_cache:
                paths = file_cache.get("path", [])
                if paths:
                    if not prompt_ratio:
                        payload["aspect_ratio"] = self._normalize_aspect_ratio(self.aspect_ratio_calculator(paths), model)
                    image_list = []
                    for p in paths:
                        with open(p, "rb") as f:
                            b64 = base64.b64encode(f.read()).decode("utf-8")
                        image_list.append({"image_url": b64})
                    payload["image_list"] = image_list
                    reference_image_count = len(image_list)
                    if memory.USER_QUOTED_IMAGE_CACHE.get(session_id):
                        logger.info(f"[{model.upper()}] 从回复引用图取参考图, count={len(paths)}")
                        logger.info(f"[{model.upper()}] 从回复引用图推断比例: {payload.get('aspect_ratio')}, count={len(paths)}")
                    else:
                        logger.info(f"[{model.upper()}] 从内存参考图取参考图, count={len(paths)}")
                        logger.info(f"[{model.upper()}] 从内存参考图推断比例: {payload.get('aspect_ratio')}, count={len(paths)}")
                if memory.USER_QUOTED_IMAGE_CACHE.get(session_id):
                    memory.USER_QUOTED_IMAGE_CACHE.pop(session_id)
                else:
                    memory.USER_IMAGE_CACHE.pop(session_id)
            elif session_images:
                if not prompt_ratio:
                    payload["aspect_ratio"] = self._normalize_aspect_ratio(
                        self._get_aspect_ratio_from_base64(session_images[0]),
                        model
                    )
                payload["image_list"] = [
                    {"image_url": url.split(",", 1)[1]} for url in session_images
                ]
                reference_image_count = len(session_images)
                logger.info(f"[{model.upper()}] 从 session 历史取参考图, count={len(session_images)}")
            else:
                # 纯文生视频，必须传 aspect_ratio
                if not prompt_ratio:
                    payload["aspect_ratio"] = self._normalize_aspect_ratio(conf().get("image_aspect_ratio", "16:9"), model)

            logger.info(
                f"[{model.upper()}] 参考素材统计: reference_images={reference_image_count}"
            )
            logger.info(
                f"[{model.upper()}] 请求参数: mode={payload.get('mode')}, resolution={resolution}, "
                f"aspect_ratio={payload.get('aspect_ratio')}, duration={payload.get('duration')}, sound={payload.get('sound')}"
            )

            resp = requests.post(
                f"{self.API_BASE}{self.ENDPOINT_OMNI}",
                headers=self._headers(),
                json=payload,
                timeout=30
            )
            resp.raise_for_status()
            data = resp.json()

            err = self._check_response_error(data)
            if err:
                logger.error(f"[{model.upper()}] 提交任务失败: {err}")
                return Reply(ReplyType.ERROR, f"可灵出视频失败：{err}")

            task_id = data.get("data", {}).get("task_id")
            if not task_id:
                return Reply(ReplyType.ERROR, "可灵视频生成失败：未获取到任务ID")

            logger.info(f"[{model.upper()}] 任务已提交, task_id={task_id}, model={model}")

            video_url, video_duration, poll_err = self._poll_task(task_id, model)
            if poll_err:
                return Reply(ReplyType.ERROR, f"可灵出视频失败：{poll_err}")

            # 视频结果注入 session
            try:
                base64_data = url_to_base64(video_url)
                session_manager.session_inject_media(
                    session_id=session_id,
                    media_type='video',
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
            return Reply(ReplyType.ERROR, f"[{model.upper()}] {e}")

    def _poll_task(self, task_id: str, model: str, max_retries=120, interval=5) -> tuple:
        query_url = f"{self.API_BASE}{self.ENDPOINT_OMNI}/{task_id}"
        retry_delay = interval
        logger.info(f"[{model.upper()}] polling task status, task_id={task_id}")

        for i in range(max_retries):
            time.sleep(retry_delay)
            try:
                resp = requests.get(query_url, headers=self._headers(), timeout=15)
                resp.raise_for_status()
                result = resp.json()
                code = result.get("code", 0)

                if code == 1303:
                    retry_delay = min(retry_delay * 2, 30)
                    logger.warning(f"[{model.upper()}] 并发超限，退避重试 {retry_delay}s")
                    continue

                err = self._check_response_error(result)
                if err:
                    return None, None, err

                data = result.get("data", {})
                status = data.get("task_status")

                if status == "succeed":
                    videos = data.get("task_result", {}).get("videos", [])
                    if videos:
                        url = videos[0].get("url")
                        duration = float(videos[0].get("duration", 5))
                        logger.info(f"[{model.upper()}] task succeeded, task_id={task_id}")
                        return url, duration, None

                elif status == "failed":
                    msg = data.get("task_status_msg", "")
                    logger.error(f"[{model.upper()}] task failed, task_id={task_id}, error={msg}")
                    return None, None, f"任务失败：{msg}"

                retry_delay = interval
                logger.info(f"[{model.upper()}] current status={status}, task_id={task_id}, retry after {retry_delay} seconds")

            except Exception as e:
                logger.warning(f"[{model.upper()}] 轮询异常: {e}")

        return None, None, "任务超时，请稍后重试"

    def _check_response_error(self, data: dict) -> str | None:
        code = data.get("code", 0)
        message = data.get("message", "")
        if code in (0, 1303):
            return None
        ERROR_MAP = {
            1000: "身份验证失败",
            1101: "账户欠费，请充值",
            1102: "资源包已用完或过期",
            1200: "请求参数非法",
            1201: "参数非法：{}",
            1301: "内容触发安全策略，请修改提示词",
            1302: "请求过快，请稍后重试",
            5000: "服务器内部错误，请稍后重试",
        }
        desc = ERROR_MAP.get(code, "未知错误：{}")
        if '{}' in desc:
            return f"[{code}] {desc.format(message)}"
        return f"[{code}] {desc}" + (f"：{message}" if message else "")

    def _normalize_duration(self, duration, model) -> str:
        normalized = str(duration).strip()
        if normalized in self._ALLOWED_VIDEO_DURATIONS:
            return normalized
        logger.warning(f"[{model.upper()}] invalid duration={duration}, fallback to 5")
        return "5"

    def _normalize_resolution(self, resolution, model) -> str:
        normalized = str(resolution).strip().lower()
        if normalized in {"720p", "1080p"}:
            return normalized
        logger.warning(f"[{model.upper()}] invalid resolution={resolution}, fallback to 720p")
        return "720p"

    def _mode_from_resolution(self, resolution: str) -> str:
        return "pro" if resolution == "1080p" else "std"

    def _normalize_aspect_ratio(self, aspect_ratio, model) -> str:
        normalized = str(aspect_ratio).strip()
        if normalized in self._ALLOWED_VIDEO_RATIOS:
            return normalized
        ratio_value = self._ratio_to_float(normalized)
        if ratio_value is None:
            logger.warning(f"[{model.upper()}] invalid aspect_ratio={aspect_ratio}, fallback to 16:9")
            return "16:9"
        allowed_values = {
            "16:9": 16 / 9,
            "9:16": 9 / 16,
            "1:1": 1.0,
        }
        mapped_ratio = min(allowed_values, key=lambda key: abs(allowed_values[key] - ratio_value))
        logger.info(f"[{model.upper()}] aspect_ratio {aspect_ratio} 不在白名单内，已映射为 {mapped_ratio}")
        return mapped_ratio

    def aspect_ratio_calculator(self, paths: list) -> str:
        ratio_map = {
            1.0: "1:1", 
            1.78: "16:9", 
            0.56: "9:16"
        }
        sizes = [Image.open(p).size for p in paths]
        best = sorted(sizes, key=lambda x: x[0] * x[1], reverse=True)[0]
        ratio = round(best[0] / best[1], 2)
        closest = min(ratio_map.keys(), key=lambda x: abs(x - ratio))
        return ratio_map[closest]

    def _get_aspect_ratio_from_base64(self, b64_url: str) -> str:
        b64_data = b64_url.split(",", 1)[1]
        img = Image.open(io.BytesIO(base64.b64decode(b64_data)))
        ratio = round(img.size[0] / img.size[1], 2)
        ratio_map = {
            1.0: "1:1", 
            1.78: "16:9", 
            0.56: "9:16"
        }
        closest = min(ratio_map.keys(), key=lambda x: abs(x - ratio))
        return ratio_map[closest]

    def _parse_aspect_ratio_from_prompt(self, prompt: str) -> str | None:
        ratio_map = {
            1.0: "1:1",
            16 / 9: "16:9",
            9 / 16: "9:16",
        }
        return parse_aspect_ratio_from_prompt(prompt, ratio_map=ratio_map, decimal_tolerance=0.7)

    def _ratio_to_float(self, ratio):
        try:
            width, height = str(ratio).split(":", 1)
            return float(width) / float(height)
        except (TypeError, ValueError, ZeroDivisionError):
            return None
