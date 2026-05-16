try:
    import grpc
except Exception:
    grpc = None

try:
    import requests
except Exception:
    requests = None

try:
    from xai_sdk.video import VideoGenerationError
except Exception:
    VideoGenerationError = None


XAI_STATUS_MESSAGES = {
    400: "Grok 请求参数有误，请检查请求体、模型、提示词或参考素材参数后重试。",
    401: "Grok API 鉴权失败，请检查 API Key 配置。",
    403: "Grok 访问被拒绝，请检查 API Key、团队权限或账号状态。",
    404: "Grok 资源不存在，请检查模型 ID 或接口地址。",
    405: "Grok 请求方法不被支持，请检查接口调用方式。",
    415: "Grok 请求媒体类型不支持，请检查请求体和 Content-Type。",
    422: "Grok 请求格式无法处理，请检查字段格式、参考素材或参数组合。",
    429: "Grok 当前请求过多，已触发限流，请降低请求频率或提升限额后再试。",
    500: "Grok 服务内部错误，请稍后重试。",
    502: "Grok 上游服务暂时不可用，请稍后重试。",
    503: "Grok 服务暂时不可用，请稍后重试。",
    504: "Grok 服务响应超时，请稍后重试。",
}

GRPC_CODE_STATUS_MAP = {
    "INVALID_ARGUMENT": 400,
    "UNAUTHENTICATED": 401,
    "PERMISSION_DENIED": 403,
    "NOT_FOUND": 404,
    "UNIMPLEMENTED": 405,
    "RESOURCE_EXHAUSTED": 429,
    "INTERNAL": 500,
    "UNKNOWN": 500,
    "UNAVAILABLE": 503,
    "DEADLINE_EXCEEDED": 504,
}


def format_grok_error(error, model, *, service_name="Grok"):
    video_message = _format_video_generation_error(error, model)
    if video_message:
        return video_message

    status_code = extract_grok_status_code(error)
    detail = extract_grok_error_detail(error)
    request_id = extract_grok_request_id(error)

    detail_text = f"（{detail}）" if detail else ""
    request_text = f" request_id={request_id}" if request_id else ""

    message = XAI_STATUS_MESSAGES.get(
        status_code,
        f"{service_name} 请求失败(status={status_code})，请稍后重试。",
    )
    if service_name != "Grok" and message.startswith("Grok "):
        message = service_name + message[len("Grok"):]
    return f"[{model.upper()}] {message}{detail_text}{request_text}"


def is_grok_sdk_error(error):
    return (
        is_grok_rpc_error(error)
        or is_grok_video_generation_error(error)
        or is_grok_http_error(error)
    )


def is_grok_rpc_error(error):
    return grpc is not None and isinstance(error, grpc.RpcError)


def is_grok_video_generation_error(error):
    return VideoGenerationError is not None and isinstance(error, VideoGenerationError)


def is_grok_http_error(error):
    return requests is not None and isinstance(error, requests.HTTPError)


def extract_grok_status_code(error):
    status_code = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)
    if status_code:
        return status_code

    code_name = extract_grok_grpc_code(error)
    if code_name:
        return GRPC_CODE_STATUS_MAP.get(code_name, code_name)

    return "unknown"


def extract_grok_grpc_code(error):
    if not is_grok_rpc_error(error):
        return None
    try:
        code = error.code()
    except Exception:
        return None
    name = getattr(code, "name", None)
    if name:
        return name
    text = str(code)
    if "." in text:
        return text.rsplit(".", 1)[-1]
    return text or None


def extract_grok_error_detail(error):
    if is_grok_video_generation_error(error):
        return getattr(error, "message", None)

    body = getattr(error, "body", None)
    if isinstance(body, dict):
        payload = body.get("error") if isinstance(body.get("error"), dict) else body
        message = payload.get("message") or payload.get("detail") or payload.get("error")
        if message:
            return str(message)

    response = getattr(error, "response", None)
    if response is not None:
        try:
            data = response.json()
            if isinstance(data, dict):
                payload = data.get("error") if isinstance(data.get("error"), dict) else data
                message = payload.get("message") or payload.get("detail") or payload.get("error")
                if message:
                    return str(message)
        except Exception:
            text = getattr(response, "text", None)
            if text:
                return str(text)

    if is_grok_rpc_error(error):
        try:
            details = error.details()
            if details:
                return str(details)
        except Exception:
            pass

    text = str(error).strip()
    return text or None


def extract_grok_request_id(error):
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if headers:
        return (
            headers.get("x-request-id")
            or headers.get("X-Request-Id")
            or headers.get("request-id")
            or headers.get("Request-Id")
        )

    if is_grok_rpc_error(error):
        try:
            trailing_metadata = error.trailing_metadata() or []
            for key, value in trailing_metadata:
                if str(key).lower() in {"x-request-id", "request-id"}:
                    return value
        except Exception:
            pass
    return None


def _format_video_generation_error(error, model):
    if not is_grok_video_generation_error(error):
        return None
    code = getattr(error, "code", None) or "UNKNOWN"
    message = getattr(error, "message", None) or str(error)
    lowered = f"{code} {message}".lower()
    if "moderation" in lowered or "safety" in lowered or "policy" in lowered:
        client_message = "Grok 视频生成未通过安全审核，请调整提示词或参考素材后重试。"
    elif "quota" in lowered or "rate" in lowered or "limit" in lowered:
        client_message = "Grok 当前请求过多或额度受限，请稍后重试。"
    elif "timeout" in lowered or "deadline" in lowered:
        client_message = "Grok 视频生成超时，请稍后重试。"
    else:
        client_message = "Grok 视频生成失败，请根据失败原因调整后重试。"
    return f"[{model.upper()}] {client_message}（{message}，code={code}）"
