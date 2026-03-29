# video/kling/kling_video.py

import io
import re
import time
import jwt
import requests
import base64

from PIL import Image

from bot.bot import Bot
from bridge.context import Context
from bridge.reply import Reply, ReplyType
from common.log import logger
from common.utils import get_chat_session_manager, get_image_urls_from_session, url_to_base64
from common import const, memory
from config import conf
from common.model_status import model_state
from common.video_status import video_state


class KlingVideoBot(Bot):

    API_BASE = "https://api-beijing.klingai.com"
    ENDPOINT_OMNI = "/v1/videos/omni-video"

    def __init__(self):
        super().__init__()
        self.access_key = conf().get("kling_access_key")
        self.secret_key = conf().get("kling_secret_key")
        self.mode = "pro"

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
            duration = str(video_state.get_video_duration(session_id))
            session_manager = get_chat_session_manager(session_id)

            logger.info(f"[{model.upper()}] query={query}, requester={session_id}")
            session_manager.session_query(query, session_id)

            payload = {
                "model_name": model,
                "prompt": query,
                "mode": self.mode,
                "duration": duration,
                "watermark_info": {"enabled": True},
                "sound": conf().get("video_sound", "off")
            }

            # 用户 prompt 中的比例优先级最高
            prompt_ratio = self._parse_aspect_ratio_from_prompt(query)
            if prompt_ratio:
                payload["aspect_ratio"] = prompt_ratio
                logger.info(f"[{model.upper()}] 从 prompt 中解析到比例: {prompt_ratio}")

            # 参考素材捕获：内存缓存优先，过期则从 session 历史捞
            file_cache = memory.USER_IMAGE_CACHE.get(session_id)
            session_images = []
            if not file_cache:
                session_images = get_image_urls_from_session(session_id, session_manager)

            if file_cache:
                paths = file_cache.get("path", [])
                if paths:
                    if not prompt_ratio:
                        payload["aspect_ratio"] = self.aspect_ratio_calculator(paths)
                    image_list = []
                    for p in paths:
                        with open(p, "rb") as f:
                            b64 = base64.b64encode(f.read()).decode("utf-8")
                        image_list.append({"image_url": b64})
                    payload["image_list"] = image_list
                    logger.info(f"[{model.upper()}] 参考图已注入 payload, count={len(paths)}")
                memory.USER_IMAGE_CACHE.pop(session_id)
            elif session_images:
                if not prompt_ratio:
                    payload["aspect_ratio"] = self._get_aspect_ratio_from_base64(session_images[0])
                payload["image_list"] = [
                    {"image_url": url.split(",", 1)[1]} for url in session_images
                ]
                logger.info(f"[{model.upper()}] 从 session 历史取参考图, count={len(session_images)}")
            else:
                # 纯文生视频，必须传 aspect_ratio
                if not prompt_ratio:
                    payload["aspect_ratio"] = conf().get("image_aspect_ratio", "16:9")

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
                    source_model=model
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
                        logger.info(f"[{model.upper()}] 视频生成成功, task_id={task_id}, url={url}")
                        return url, duration, None

                elif status == "failed":
                    msg = data.get("task_status_msg", "")
                    return None, None, f"任务失败：{msg}"

                retry_delay = interval
                logger.debug(f"[{model.upper()}] 轮询中 ({i+1}/{max_retries}), status={status}")

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
            1.78: "16:9", 
            0.56: "9:16"
        }
        decimal_pattern = r'(?<!\d)(\d+\.\d+)(?!\d)'
        for match in re.finditer(decimal_pattern, prompt):
            ratio = round(float(match.group(1)), 2)
            closest = min(ratio_map.keys(), key=lambda x: abs(x - ratio))
            if abs(closest - ratio) <= 0.15:
                return ratio_map[closest]
        pattern = r'(\d+)\s*(?::|：|比)\s*(\d+)'
        match = re.search(pattern, prompt)
        if not match:
            return None
        w, h = int(match.group(1)), int(match.group(2))
        ratio = round(w / h, 2)
        closest = min(ratio_map.keys(), key=lambda x: abs(x - ratio))
        if abs(closest - ratio) > 0.3:
            return None
        return ratio_map[closest]
