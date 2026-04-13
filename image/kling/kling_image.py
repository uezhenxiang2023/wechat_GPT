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
from common.aspect_ratio import parse_aspect_ratio_from_prompt
from common.log import logger
from common.utils import get_chat_session_manager, url_to_base64
from common import const, memory
from config import conf
from common.model_status import model_state

class KlingImageBot(Bot):

    API_BASE = "https://api-beijing.klingai.com"
    ENDPOINT_GENERATIONS = "/v1/images/generations"
    ENDPOINT_OMNI = "/v1/images/omni-image"
    _MAX_REFERENCE_IMAGE_BYTES = int(9.5 * 1024 * 1024)
    _SERIES_IMAGE_MIN_COUNT = 2
    _SERIES_IMAGE_MAX_COUNT = 9
    _SERIES_IMAGE_CN_NUM_MAP = {
        "两": 2,
        "俩": 2,
        "仨": 3,
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }

    def __init__(self):
        super().__init__()
        self.access_key = conf().get("kling_access_key")
        self.secret_key = conf().get("kling_secret_key")
        self.image_aspect_ratio = self._normalize_aspect_ratio(conf().get("image_aspect_ratio", "16:9"))

    def _get_token(self) -> str:
        headers = {
            "alg": "HS256", 
            "typ": "JWT"
        }
        
        payload = {
            "iss": self.access_key,
            "exp": int(time.time()) + 1800,
            "nbf": int(time.time()) - 5
        }

        api_token = jwt.encode(payload, self.secret_key, headers=headers)
        return api_token

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._get_token()}"
        }

    def _get_endpoint(self, model: str) -> str:
        """根据模型名称返回对应端点"""
        if model in const.KLING_OMNI_IMAGE_LIST:
            return self.ENDPOINT_OMNI
        return self.ENDPOINT_GENERATIONS

    def reply(self, query, context: Context = None) -> Reply:
        try:
            session_id = context["session_id"]
            model = model_state.get_image_model(session_id) or const.KLING_IMAGE_O1
            session_manager = get_chat_session_manager(session_id)

            logger.info(f"[{model.upper()}] query={query}, requester={session_id}")
            session_manager.session_query(query, session_id)

            endpoint = self._get_endpoint(model)
            payload = {
                "model_name": model,
                "prompt": query,
                "resolution": self._normalize_resolution(model_state.get_image_size(session_id)),
                "n": 1,
                "result_type": "single",
                "aspect_ratio": self.image_aspect_ratio
            }
            sequential_image_count = self._parse_sequential_image_count_from_prompt(query)
            if sequential_image_count:
                payload.update({
                    "result_type": "series",
                    "series_amount": sequential_image_count
                })
                payload.pop("n", None)
                logger.info(
                    f"[{model.upper()}] 从 prompt 中解析到组图数量: {sequential_image_count}, "
                    "已启用 result_type=series"
                )
            # 用户 prompt 中的比例优先级最高
            prompt_ratio = self._parse_aspect_ratio_from_prompt(query)
            if prompt_ratio:
                payload["aspect_ratio"] = prompt_ratio
                logger.info(f"[{model.upper()}] 从 prompt 中解析到比例: {prompt_ratio}")

            # 参考图捕获
            file_cache = memory.USER_QUOTED_IMAGE_CACHE.get(session_id)
            if not file_cache:
                file_cache = memory.USER_IMAGE_CACHE.get(session_id)
            if not file_cache:
                logger.info(
                    f"[{model.upper()}] 当前为文生图模式, aspect_ratio={payload['aspect_ratio']}, image_size={payload['resolution']}"
                )
            elif file_cache:
                paths = file_cache.get("path", [])
                if paths:
                    # 如果用户没设置图片比例，则自动从缓存图片的比例
                    if not prompt_ratio:
                        payload["aspect_ratio"] = self.aspect_ratio_calculator(paths)
                    if endpoint == self.ENDPOINT_OMNI:
                        # omni-image: image_list，支持多图
                        image_list = []
                        for p in paths:
                            with open(p, "rb") as f:
                                b64 = base64.b64encode(f.read()).decode("utf-8")
                            image_list.append({"image": self._ensure_reference_image_within_limit(b64, model)})
                        payload["image_list"] = image_list
                    else:
                        # generations: image，只取第一张
                        with open(paths[0], "rb") as f:
                            b64 = base64.b64encode(f.read()).decode("utf-8")
                        payload["image"] = self._ensure_reference_image_within_limit(b64, model)
                    logger.info(
                        f"[{model.upper()}] request summary: mode=edit, reference_count={len(paths)}, "
                        f"aspect_ratio={payload['aspect_ratio']}, image_size={payload['resolution']}"
                    )
                    logger.info(f"[{model.upper()}] 参考图已注入 payload, model={model}, count={len(paths)}")
                    if memory.USER_QUOTED_IMAGE_CACHE.get(session_id):
                        logger.info(f"[{model.upper()}] 从回复引用图取参考图推断比例: {payload['aspect_ratio']}")
                    else:
                        logger.info(f"[{model.upper()}] 从内存参考图推断比例: {payload['aspect_ratio']}")
                if memory.USER_QUOTED_IMAGE_CACHE.get(session_id):
                    memory.USER_QUOTED_IMAGE_CACHE.pop(session_id)
                else:
                    memory.USER_IMAGE_CACHE.pop(session_id)

            resp = requests.post(
                f"{self.API_BASE}{endpoint}",
                headers=self._headers(),
                json=payload,
                timeout=30
            )
            resp.raise_for_status()
            data = resp.json()

            # 检查业务错误码
            err = self._check_response_error(data)
            if err:
                logger.info(f"[{model.upper()}] 提交任务失败: {err}, resp={data}")
                return Reply(ReplyType.ERROR, f"[{model.upper()}]出图失败：{err}")

            task_id = data.get("data", {}).get("task_id")
            if not task_id:
                logger.error(f"[{model.upper()}] 未获取到 task_id, resp={data}")
                return Reply(ReplyType.ERROR, f"[{model.upper()}]图片生成失败：未获取到任务ID")

            logger.info(f"[{model.upper()}] 任务已提交, task_id={task_id}, model={model}, endpoint={endpoint}")

            image_urls, poll_err = self._poll_task(task_id, endpoint, model)
            if poll_err:
                return Reply(ReplyType.ERROR, f"[{model.upper()}]出图失败：{poll_err}")

            # 图片生成结果注入 session 上下文
            try:
                for image_url in image_urls:
                    base64_data = url_to_base64(image_url)
                    session_manager.session_inject_media(
                        session_id=session_id,
                        media_type='image',
                        data=base64_data,
                        source_model=model
                    )
                logger.info(
                    f"[{model.upper()}] image injected to session, model={model}, "
                    f"session_id={session_id}, image_count={len(image_urls)}"
                )
            except Exception as e:
                logger.warning(f"[{model.upper()}] failed to inject image to session: {e}")

            logger.info(f"[{model.upper()}] image generation finished, image_count={len(image_urls)}")
            if len(image_urls) == 1:
                return Reply(ReplyType.IMAGE_URL, image_urls[0])
            return Reply(ReplyType.IMAGE_URL, image_urls)

        except Exception as e:
            logger.error(f"[{model.upper()}] fetch reply error: {e}")
            return Reply(ReplyType.ERROR, f"[{model.upper()}] {e}")

    def _normalize_resolution(self, resolution: str) -> str:
        normalized = str(resolution).strip().lower()
        if normalized in {"1k", "2k", "4k"}:
            return normalized
        logger.warning(f"[Kling_Image] invalid resolution={resolution}, fallback to 1k")
        return "1k"

    def _normalize_aspect_ratio(self, aspect_ratio: str) -> str:
        normalized = str(aspect_ratio).strip().lower()
        ratio_alias_map = {
            "16:9": "16:9",
            "9:16": "9:16",
            "1:1": "1:1",
            "4:3": "4:3",
            "3:4": "3:4",
            "3:2": "3:2",
            "2:3": "2:3",
            "21:9": "21:9",
            "auto": "auto",
        }
        if normalized in ratio_alias_map:
            return ratio_alias_map[normalized]
        logger.warning(f"[Kling_Image] invalid aspect_ratio={aspect_ratio}, fallback to 16:9")
        return "16:9"

    def _ensure_reference_image_within_limit(self, image_base64, model):
        image_url = f"data:image/jpeg;base64,{image_base64}"
        original_size = len(image_url.encode("utf-8"))
        if original_size <= self._MAX_REFERENCE_IMAGE_BYTES:
            return image_base64

        logger.warning(
            f"[{model.upper()}] reference image too large, start compressing, size={original_size} bytes, "
            f"limit={self._MAX_REFERENCE_IMAGE_BYTES}"
        )
        compressed_base64 = self._compress_base64_image(image_base64, model)
        compressed_size = len(f'data:image/jpeg;base64,{compressed_base64}'.encode("utf-8"))
        logger.info(
            f"[{model.upper()}] reference image compressed, before={original_size} bytes, "
            f"after={compressed_size} bytes"
        )
        return compressed_base64

    def _compress_base64_image(self, image_base64, model):
        try:
            image = Image.open(io.BytesIO(base64.b64decode(image_base64)))
            if image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGB")

            quality = 90
            width, height = image.size
            resized = image
            while True:
                buf = io.BytesIO()
                resized.save(buf, format="JPEG", quality=quality, optimize=True)
                encoded = base64.b64encode(buf.getvalue()).decode("utf-8")
                encoded_size = len(f"data:image/jpeg;base64,{encoded}".encode("utf-8"))
                if encoded_size <= self._MAX_REFERENCE_IMAGE_BYTES:
                    return encoded
                if quality > 55:
                    quality -= 10
                    continue
                width = max(int(width * 0.85), 512)
                height = max(int(height * 0.85), 512)
                if (width, height) == resized.size:
                    logger.warning(f"[{model.upper()}] reference image still exceeds limit after compression")
                    return encoded
                resized = image.resize((width, height), Image.LANCZOS)
                quality = 85
        except Exception as e:
            logger.warning(f"[{model.upper()}] failed to compress reference image: {e}")
            return image_base64

    def _check_response_error(self, data: dict) -> str | None:
        """检查响应中的业务错误码，返回错误描述或 None"""
        code = data.get("code", 0)
        message = data.get("message", "")
        if code in (0, 1303): # 0 成功，1303 单独在轮询里处理退避
            return None
        ERROR_MAP = {
            1000: "身份验证失败，请检查 Authorization 是否正确",
            1001: "Authorization 为空，请在请求头中填写正确的 Authorization",
            1002: "Authorization 值非法，请检查 AK/SK 配置",
            1003: "Authorization 未到有效时间，请等待生效或重新签发 Token",
            1004: "Authorization 已失效，请重新签发 Token",
            1100: "账户异常，请检查账户配置信息",
            1101: "账户欠费，请充值确保余额充足",
            1102: "资源包已用完或过期，请购买额外资源包或开通后付费",
            1103: "账户无权限访问该接口或模型，请检查账户权限",
            1200: "请求参数非法，请检查请求参数是否正确",
            1201: "参数非法：{}",
            1202: "请求 method 无效，请查看接口文档使用正确的 method",
            1203: "请求的资源不存在：{}",
            1300: "触发平台策略，请检查是否触发平台策略",
            1301: "内容触发安全策略，请修改提示词后重试",
            1302: "请求过快，超过速率限制，请降低频率或稍后重试",
            1304: "触发 IP 白名单策略，请联系客服",
            5000: "服务器内部错误，请稍后重试",
            5001: "服务器暂时不可用（维护中），请稍后重试",
            5002: "服务器内部超时，请稍后重试",
        }
        desc = ERROR_MAP.get(code, "未知错误：{}")
        # 有 {} 占位符的用 message 填充，没有的直接追加 message
        if '{}' in desc:
            return f"[{code}] {desc.format(message)}"
        else:
            return f"[{code}] {desc}" + (f"：{message}" if message else "")

    def _poll_task(self, task_id: str, endpoint: str, model: str, max_retries=120, interval=5) -> tuple:
        query_url = f"{self.API_BASE}{endpoint}/{task_id}"
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
                    logger.warning(f"[{model.upper()}] 并发超限，退避重试 {retry_delay}s, task_id={task_id}")
                    continue

                err = self._check_response_error(result)
                if err:
                    logger.error(f"[{model.upper()}] 轮询出错: {err}, task_id={task_id}")
                    return None, err

                data = result.get("data", {})
                status = data.get("task_status")

                if status == "succeed":
                    images = data.get("task_result", {}).get("images", [])
                    if images:
                        image_urls = [item.get("url") for item in images if item.get("url")]
                        if image_urls:
                            logger.info(
                                f"[{model.upper()}] 图片生成成功, task_id={task_id}, image_count={len(image_urls)}"
                            )
                            return image_urls, None

                elif status == "failed":
                    status_msg = data.get("task_status_msg", "")
                    logger.error(f"[{model.upper()}] 任务失败, task_id={task_id}, msg={status_msg}")
                    return None, f"任务失败：{status_msg}"

                retry_delay = interval
                logger.debug(f"[{model.upper()}] 轮询中 ({i+1}/{max_retries}), status={status}, task_id={task_id}")

            except Exception as e:
                logger.warning(f"[{model.upper()}] 轮询异常: {e}")

        logger.error(f"[{model.upper()}] 任务超时, task_id={task_id}")
        return None, "任务超时，请稍后重试"

    def aspect_ratio_calculator(self, paths: list) -> str:
        """根据参考图尺寸推断最佳宽高比"""
        ratio_map = {
            1.0:  "1:1",
            1.33: "4:3",
            0.75: "3:4",
            1.78: "16:9",
            0.56: "9:16",
            1.5:  "3:2",
            0.67: "2:3",
            2.33: "21:9"
        }

        # 如果内存中有多图获，取所有图片中最大尺寸的宽高比
        sizes = []
        for p in paths:
            img = Image.open(p)
            sizes.append(img.size)  # (width, height)
        best = sorted(sizes, key=lambda x: x[0] * x[1], reverse=True)[0]
        ratio = round(best[0] / best[1], 2)

        # 跟ratio_map中的预置参数比较，取最相似的值
        closest = min(ratio_map.keys(), key=lambda x: abs(x - ratio))
        return ratio_map[closest]

    def _parse_aspect_ratio_from_prompt(self, prompt: str) -> str | None:
        if not prompt:
            return None
        if re.search(r"\bauto\b|自动(?:比例|画幅|宽高比)?", prompt, re.IGNORECASE):
            return "auto"
        aspect_ratio = parse_aspect_ratio_from_prompt(prompt)
        return self._normalize_aspect_ratio(aspect_ratio) if aspect_ratio else None

    def _parse_sequential_image_count_from_prompt(self, prompt):
        if not prompt:
            return None

        normalized_prompt = prompt.replace("张图", "张").replace("幅图", "幅")

        digit_patterns = [
            r"(?:组图|一组|做|生成|输出|画|给我|来|要)\s*(\d{1,2})\s*(?:张|幅)",
            r"(\d{1,2})\s*(?:张|幅)\s*(?:组图|系列图|连环图)",
            r"(?:共|一共|最多|至多|最多生成)\s*(\d{1,2})\s*(?:张|幅)",
        ]
        for pattern in digit_patterns:
            match = re.search(pattern, normalized_prompt, re.IGNORECASE)
            if match:
                return self._clamp_sequential_image_count(int(match.group(1)))

        cn_pattern = r"(两|俩|仨|[一二三四五六七八九十])\s*(?:张|幅)\s*(?:组图|系列图|连环图)?"
        match = re.search(cn_pattern, normalized_prompt)
        if match:
            parsed = self._SERIES_IMAGE_CN_NUM_MAP.get(match.group(1))
            if parsed:
                return self._clamp_sequential_image_count(parsed)

        return None

    def _clamp_sequential_image_count(self, count):
        if count < self._SERIES_IMAGE_MIN_COUNT:
            return None
        return min(count, self._SERIES_IMAGE_MAX_COUNT)
