import base64
import io
import time

from PIL import Image
from luma_agents import APIStatusError, Luma

from bot.bot import Bot
from bridge.context import Context
from bridge.reply import Reply, ReplyType
from common import const, memory
from common.aspect_ratio import parse_aspect_ratio_from_prompt
from common.log import logger
from common.model_status import model_state
from common.utils import get_chat_session_manager, url_to_base64
from config import conf


_LUMA_IMAGE_RATIO_MAP = {
    "3:1": 3 / 1,
    "2:1": 2 / 1,
    "16:9": 16 / 9,
    "3:2": 3 / 2,
    "1:1": 1.0,
    "2:3": 2 / 3,
    "9:16": 9 / 16,
    "1:2": 1 / 2,
    "1:3": 1 / 3,
}
_LUMA_REFERENCE_IMAGE_MAX_BYTES = 50 * 1024 * 1024
_LUMA_GENERATION_IMAGE_REF_MAX_COUNT = 9
_LUMA_EDITING_IMAGE_REF_MAX_COUNT = _LUMA_GENERATION_IMAGE_REF_MAX_COUNT - 1


class LumaClientFacingError(Exception):
    pass


class LumaImageBot(Bot):
    def __init__(self):
        super().__init__()
        api_key = conf().get("luma_agents_api_key")
        self.client = Luma(
            auth_token=api_key,
            timeout=conf().get("request_timeout", 180),
        )

    def reply(self, query, context: Context = None) -> Reply:
        error_model = const.UNI_1
        try:
            session_id = context["session_id"]
            model = model_state.get_image_model(session_id) or const.UNI_1
            error_model = model
            session_manager = get_chat_session_manager(session_id)
            logger.info(f"[{model.upper()}] query={query}, requester={session_id}")
            session_manager.session_query(query, session_id)

            params, request_meta = self._build_generation_params(query, session_id, model)
            logger.info(
                f"[{model.upper()}] request summary: mode={request_meta['mode']}, "
                f"reference_count={request_meta['reference_count']}, "
                f"aspect_ratio={params.get('aspect_ratio')}, image_size={request_meta['image_size']}"
            )

            try:
                generation = self.client.generations.create(**params)
            except APIStatusError as e:
                error_message = self._format_sync_api_error(e, model, endpoint="POST")
                logger.warning(f"[{model.upper()}] Luma create request failed: {error_message}")
                return Reply(ReplyType.ERROR, error_message)
            generation = self._poll_generation(generation, model)
            image_urls = [
                item.url for item in (getattr(generation, "output", None) or [])
                if getattr(item, "url", None)
            ]
            if not image_urls:
                raise ValueError("Luma image response missing image url")

            try:
                for image_url in image_urls:
                    base64_data = url_to_base64(image_url)
                    session_manager.session_inject_media(
                        session_id=session_id,
                        media_type="image",
                        data=base64_data,
                        source_model=model,
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
        except LumaClientFacingError as e:
            logger.warning(f"[{error_model.upper()}] client-facing luma error: {e}")
            return Reply(ReplyType.ERROR, str(e))
        except Exception as e:
            logger.error(f"[{error_model.upper()}] fetch reply error: {e}")
            return Reply(ReplyType.ERROR, f"[{error_model.upper()}] {e}")

    def _build_generation_params(self, query, session_id, model):
        prompt_ratio = self._parse_aspect_ratio_from_prompt(query, model)
        if prompt_ratio:
            logger.info(f"[{model.upper()}] 从 prompt 中解析到比例: {prompt_ratio}")

        image_mode = model_state.get_image_mode(session_id)
        normalized_image_mode = str(image_mode).lower()
        request_mode = "edit" if normalized_image_mode == "editing" else "generate"
        image_size = self._normalize_resolution(model_state.get_image_size(session_id), model)
        quoted_cache = memory.USER_QUOTED_IMAGE_CACHE.get(session_id)
        file_cache = memory.USER_IMAGE_CACHE.get(session_id)

        if normalized_image_mode == "editing" and quoted_cache:
            source_refs = self._build_image_refs(
                quoted_cache.get("files", []),
                model,
                request_mode=request_mode,
            )
            reference_refs = (
                self._build_image_refs(
                    file_cache.get("files", []),
                    model,
                    request_mode=request_mode,
                    max_count=_LUMA_EDITING_IMAGE_REF_MAX_COUNT,
                )
                if file_cache
                else []
            )
            memory.USER_QUOTED_IMAGE_CACHE.pop(session_id, None)
            if file_cache:
                memory.USER_IMAGE_CACHE.pop(session_id, None)
            if source_refs:
                reference_count = len(source_refs) + len(reference_refs)
                aspect_ratio = prompt_ratio or self._infer_aspect_ratio_from_images(quoted_cache.get("files", []), model)
                if not prompt_ratio:
                    logger.info(
                        f"[{model.upper()}] Editing 模式使用回复引用图作为 source 推断比例: {aspect_ratio}, "
                        f"source_count={len(source_refs)}, image_ref_count={len(reference_refs)}"
                    )
                return self._build_edit_params(
                    query,
                    model,
                    aspect_ratio,
                    source_refs,
                    image_mode,
                    reference_refs=reference_refs,
                ), {
                    "mode": request_mode,
                    "reference_count": reference_count,
                    "image_size": image_size,
                }

        if quoted_cache:
            image_refs = self._build_image_refs(
                quoted_cache.get("files", []),
                model,
                request_mode=request_mode,
                max_count=_LUMA_GENERATION_IMAGE_REF_MAX_COUNT,
            )
            memory.USER_QUOTED_IMAGE_CACHE.pop(session_id, None)
            if image_refs:
                aspect_ratio = prompt_ratio or self._infer_aspect_ratio_from_images(quoted_cache.get("files", []), model)
                if not prompt_ratio:
                    logger.info(
                        f"[{model.upper()}] 从回复引用图取参考图推断比例: {aspect_ratio}, count={len(image_refs)}"
                    )
                return self._build_edit_params(query, model, aspect_ratio, image_refs, image_mode), {
                    "mode": request_mode,
                    "reference_count": len(image_refs),
                    "image_size": image_size,
                }

        if file_cache:
            image_refs = self._build_image_refs(
                file_cache.get("files", []),
                model,
                request_mode=request_mode,
                max_count=_LUMA_GENERATION_IMAGE_REF_MAX_COUNT,
            )
            memory.USER_IMAGE_CACHE.pop(session_id, None)
            if image_refs:
                aspect_ratio = prompt_ratio or self._infer_aspect_ratio_from_images(file_cache.get("files", []), model)
                if not prompt_ratio:
                    logger.info(f"[{model.upper()}] 从内存参考图推断比例: {aspect_ratio}, count={len(image_refs)}")
                return self._build_edit_params(query, model, aspect_ratio, image_refs, image_mode), {
                    "mode": request_mode,
                    "reference_count": len(image_refs),
                    "image_size": image_size,
                }

        aspect_ratio = prompt_ratio or self._normalize_aspect_ratio(conf().get("image_aspect_ratio", "16:9"), model)
        logger.info(f"[{model.upper()}] 当前为文生图模式, aspect_ratio={aspect_ratio}, image_size={image_size}")
        return {
            "prompt": query,
            "model": model,
            "type": "image",
            "aspect_ratio": aspect_ratio,
            "output_format": "jpeg",
        }, {
            "mode": "generate",
            "reference_count": 0,
            "image_size": image_size,
        }

    def _format_sync_api_error(self, error, model, endpoint="POST"):
        status_code = self._extract_luma_status_code(error)
        detail = self._extract_luma_error_detail(error)
        retry_after = self._extract_luma_header(error, "Retry-After")
        request_id = self._extract_luma_header(error, "X-Request-Id")

        detail_text = f"（{detail}）" if detail else ""
        retry_text = f"，建议 {retry_after} 秒后再试" if retry_after else ""
        request_text = f" request_id={request_id}" if request_id else ""

        if endpoint == "GET":
            status_messages = {
                401: f"Luma 轮询鉴权失败{detail_text}，请检查 API Key 配置。",
                404: f"Luma 任务不存在或无权访问{detail_text}，请确认任务 ID 和 API Key 是否匹配。",
            }
        else:
            status_messages = {
                400: f"Luma 请求参数有误{detail_text}，请调整后重试。",
                401: f"Luma API 鉴权失败{detail_text}，请检查 API Key 配置。",
                402: f"Luma 账户额度不足{detail_text}，请充值后再试。",
                403: f"Luma 访问被拒绝{detail_text}，请检查账号状态或权限。",
                413: f"参考图超过 Luma 单张 50MB 限制{detail_text}，请压缩图片后再试。",
                422: f"Luma 无法处理当前请求{detail_text}，请检查参考图是否有效、可读取，或调整参数组合。",
                429: f"Luma 当前请求过多{detail_text}{retry_text}。",
                502: f"Luma 上游服务暂时不可用{detail_text}，请稍后重试。",
                503: f"Luma 图片接入服务暂时不可用{detail_text}，请稍后重试。",
            }
        message = status_messages.get(
            status_code,
            f"Luma {endpoint} 请求失败(status={status_code}){detail_text}，请稍后重试。",
        )
        return f"[{model.upper()}] {message}{request_text}"

    def _format_async_failure(self, generation, model):
        generation_id = getattr(generation, "id", None)
        reason = getattr(generation, "failure_reason", None) or "Luma 未返回失败原因"
        code = getattr(generation, "failure_code", None) or "unknown"

        code_messages = {
            "content_moderated": "Luma 内容审核未通过，请调整提示词或参考图后重新提交。",
            "generation_failed": "Luma 生成过程中发生临时错误，可以稍后重试同一请求。",
            "budget_exhausted": "Luma 账户额度在生成过程中耗尽，请充值后重新提交。",
            "output_not_found": "Luma 生成结果暂时无法取回，可以稍后重试同一请求。",
        }
        message = code_messages.get(code, "Luma 生成任务失败，请根据失败原因调整后重试。")
        id_text = f" generation_id={generation_id}" if generation_id else ""
        return f"[{model.upper()}] {message}（{reason}，failure_code={code}）{id_text}"

    def _extract_luma_status_code(self, error):
        status_code = getattr(error, "status_code", None)
        response = getattr(error, "response", None)
        if status_code is None and response is not None:
            status_code = getattr(response, "status_code", None)
        return status_code or "unknown"

    def _extract_luma_error_detail(self, error):
        body = getattr(error, "body", None)
        if isinstance(body, dict):
            detail = body.get("detail")
            if detail:
                return str(detail)

        response = getattr(error, "response", None)
        if response is not None:
            try:
                data = response.json()
                if isinstance(data, dict) and data.get("detail"):
                    return str(data["detail"])
            except Exception:
                pass

        message = str(error).strip()
        return message or "Luma API 未返回错误详情"

    def _extract_luma_header(self, error, header_name):
        response = getattr(error, "response", None)
        headers = getattr(response, "headers", None) if response is not None else None
        if not headers:
            return None
        return headers.get(header_name) or headers.get(header_name.lower())

    def _build_edit_params(self, query, model, aspect_ratio, image_refs, image_mode, reference_refs=None):
        reference_refs = (reference_refs or [])[:_LUMA_EDITING_IMAGE_REF_MAX_COUNT]
        normalized_image_mode = str(image_mode).lower()
        params = {
            "prompt": query,
            "model": model,
            "type": "image_edit" if normalized_image_mode == "editing" else "image",
            "aspect_ratio": aspect_ratio,
            "output_format": "jpeg",
        }
        if normalized_image_mode == "editing":
            params["source"] = image_refs[0]
            params["image_ref"] = reference_refs[:_LUMA_EDITING_IMAGE_REF_MAX_COUNT]
            logger.info(
                f"[{model.upper()}] Luma Editing 模式: source=1, image_ref={len(params.get('image_ref', []))}"
            )
        else:
            params["image_ref"] = image_refs
            logger.info(f"[{model.upper()}] Luma Generation 模式: image_ref={len(image_refs)}")
        return params

    def _build_image_refs(self, images, model, request_mode=None, max_count=_LUMA_EDITING_IMAGE_REF_MAX_COUNT):
        image_refs = []
        for image in images:
            image_ref = self._encode_pil_image_ref(image, model)
            if image_ref:
                image_refs.append(image_ref)
        if len(image_refs) > max_count:
            logger.info(
                f"[{model.upper()}] {request_mode} 模式参考图上限为{max_count}, "
                f"当前参考图数量 {len(image_refs)} 张，已裁剪为 {max_count} 张"
            )
        return image_refs[:max_count]

    def _encode_pil_image_ref(self, image, model):
        try:
            if image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGB")
            buf = io.BytesIO()
            image.save(buf, format="JPEG", quality=95)
            image_data = self._ensure_reference_image_within_limit(buf.getvalue(), model)
            return {
                "data": base64.b64encode(image_data).decode("utf-8"),
                "media_type": "image/jpeg",
            }
        except Exception as e:
            logger.warning(f"[{model.upper()}] failed to encode cached image: {e}")
            return None

    def _ensure_reference_image_within_limit(self, image_data, model):
        if len(image_data) <= _LUMA_REFERENCE_IMAGE_MAX_BYTES:
            return image_data
        logger.warning(
            f"[{model.upper()}] reference image too large, start compressing, "
            f"size={len(image_data)} bytes, limit={_LUMA_REFERENCE_IMAGE_MAX_BYTES}"
        )
        return self._compress_image_bytes(image_data, model)

    def _compress_image_bytes(self, image_data, model):
        try:
            image = Image.open(io.BytesIO(image_data))
            if image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGB")

            quality = 90
            width, height = image.size
            resized = image
            while True:
                buf = io.BytesIO()
                resized.save(buf, format="JPEG", quality=quality, optimize=True)
                compressed = buf.getvalue()
                if len(compressed) <= _LUMA_REFERENCE_IMAGE_MAX_BYTES:
                    logger.info(f"[{model.upper()}] reference image compressed, size={len(compressed)} bytes")
                    return compressed
                if quality > 55:
                    quality -= 10
                    continue
                width = max(int(width * 0.85), 512)
                height = max(int(height * 0.85), 512)
                if (width, height) == resized.size:
                    logger.warning(f"[{model.upper()}] reference image still exceeds limit after compression")
                    return compressed
                resized = image.resize((width, height), Image.LANCZOS)
                quality = 85
        except Exception as e:
            logger.warning(f"[{model.upper()}] failed to compress reference image: {e}")
            return image_data

    def _poll_generation(self, generation, model, max_retries=120, interval=2):
        last_log_time = 0
        for i in range(max_retries):
            state = getattr(generation, "state", None)
            if state == "completed":
                return generation
            if state == "failed":
                raise LumaClientFacingError(self._format_async_failure(generation, model))

            time.sleep(interval)
            try:
                generation = self.client.generations.get(generation.id)
            except APIStatusError as e:
                error_message = self._format_sync_api_error(e, model, endpoint="GET")
                logger.warning(f"[{model.upper()}] Luma poll request failed: {error_message}")
                raise LumaClientFacingError(error_message)
            now = time.time()
            if now - last_log_time >= 10:
                logger.info(
                    f"[{model.upper()}] 轮询中 ({i + 1}/{max_retries}), "
                    f"state={generation.state}, id={generation.id}"
                )
                last_log_time = now

        raise TimeoutError("任务超时，请稍后重试")

    def _normalize_resolution(self, resolution, model):
        normalized = str(resolution).strip().lower()
        if normalized in {"1k", "2k"}:
            return normalized
        logger.warning(f"[{model.upper()}] invalid resolution={resolution}, fallback to 1k")
        return "1k"

    def _normalize_aspect_ratio(self, aspect_ratio, model):
        normalized = str(aspect_ratio).strip()
        if normalized in _LUMA_IMAGE_RATIO_MAP:
            return normalized
        logger.warning(f"[{model.upper()}] invalid aspect_ratio={aspect_ratio}, fallback to 16:9")
        return "16:9"

    def _parse_aspect_ratio_from_prompt(self, prompt, model):
        ratio_candidates = {value: key for key, value in _LUMA_IMAGE_RATIO_MAP.items()}
        aspect_ratio = parse_aspect_ratio_from_prompt(
            prompt,
            ratio_map=ratio_candidates,
            decimal_tolerance=0.25,
            ratio_tolerance=0.3,
        )
        return self._normalize_aspect_ratio(aspect_ratio, model) if aspect_ratio else None

    def _infer_aspect_ratio_from_images(self, images, model):
        sizes = []
        for image in images:
            try:
                sizes.append(image.size)
            except Exception as e:
                logger.warning(f"[{model.upper()}] failed to infer aspect ratio from image: {e}")
        if not sizes:
            return self._normalize_aspect_ratio(conf().get("image_aspect_ratio", "16:9"), model)
        best_size = sorted(sizes, key=lambda size: size[0] * size[1], reverse=True)[0]
        ratio = round(best_size[0] / best_size[1], 4)
        return min(_LUMA_IMAGE_RATIO_MAP, key=lambda key: abs(_LUMA_IMAGE_RATIO_MAP[key] - ratio))
