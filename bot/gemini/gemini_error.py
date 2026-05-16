try:
    from google.genai import errors as genai_errors
except Exception:
    genai_errors = None

try:
    import httpx
except Exception:
    httpx = None


GEMINI_STATUS_MESSAGES = {
    400: "Gemini 请求参数有误，请检查请求体、模型、提示词或参考素材参数后重试。",
    403: "Gemini API 权限不足，请检查 API Key、模型权限或账号状态。",
    404: "Gemini 请求的资源不存在，请检查模型 ID、文件或接口版本。",
    429: "Gemini 当前请求过多或额度耗尽，请检查限额后稍后重试。",
    500: "Gemini 服务内部错误，请稍后重试。",
    503: "Gemini 服务暂时不可用或容量不足，请稍后重试，或临时切换到其他模型。",
    504: "Gemini 处理超时，请缩短输入内容或稍后重试。",
}

GEMINI_BACKEND_STATUS_MESSAGES = {
    "INVALID_ARGUMENT": "Gemini 请求参数有误，请检查请求体、模型、提示词或参考素材参数后重试。",
    "FAILED_PRECONDITION": "Gemini 当前请求不满足前置条件，请确认账号区域、计费状态或项目配置。",
    "PERMISSION_DENIED": "Gemini API 权限不足，请检查 API Key、模型权限或账号状态。",
    "NOT_FOUND": "Gemini 请求的资源不存在，请检查模型 ID、文件或接口版本。",
    "RESOURCE_EXHAUSTED": "Gemini 当前请求过多或额度耗尽，请检查限额后稍后重试。",
    "INTERNAL": "Gemini 服务内部错误，请稍后重试。",
    "UNAVAILABLE": "Gemini 服务暂时不可用或容量不足，请稍后重试，或临时切换到其他模型。",
    "DEADLINE_EXCEEDED": "Gemini 处理超时，请缩短输入内容或稍后重试。",
}


def format_gemini_error(error, model, *, service_name="Gemini"):
    if is_gemini_video_generation_error(error):
        return _format_gemini_video_generation_error(error, model)

    status_code = extract_gemini_status_code(error)
    backend_status = extract_gemini_backend_status(error)
    message = extract_gemini_error_message(error)
    request_id = extract_gemini_request_id(error)

    base_message = GEMINI_BACKEND_STATUS_MESSAGES.get(backend_status)
    if not base_message:
        base_message = GEMINI_STATUS_MESSAGES.get(
            status_code,
            f"{service_name} 请求失败(status={status_code})，请稍后重试。",
        )
    if service_name != "Gemini" and base_message.startswith("Gemini "):
        base_message = service_name + base_message[len("Gemini "):]

    message_text = f"（{message}）" if message else ""
    status_text = f"，状态={backend_status}" if backend_status else ""
    request_text = f" request_id={request_id}" if request_id else ""
    return f"[{model.upper()}] {base_message}{message_text}{status_text}{request_text}"


def is_gemini_sdk_error(error):
    return (
        is_gemini_api_error(error)
        or is_gemini_video_generation_error(error)
        or is_gemini_timeout_error(error)
    )


def is_gemini_api_error(error):
    return (
        genai_errors is not None
        and isinstance(error, getattr(genai_errors, "APIError", ()))
    )


def is_gemini_video_generation_error(error):
    return error.__class__.__name__ == "GeminiVideoGenerationError"


def is_gemini_timeout_error(error):
    if isinstance(error, TimeoutError):
        return True
    if httpx is None:
        return False
    return isinstance(error, (httpx.TimeoutException, httpx.ConnectError))


def extract_gemini_status_code(error):
    code = getattr(error, "code", None) or getattr(error, "status_code", None)
    if code:
        return code
    response = getattr(error, "response", None)
    if response is not None:
        return getattr(response, "status_code", None) or "unknown"
    if is_gemini_timeout_error(error):
        return 504
    return "unknown"


def extract_gemini_backend_status(error):
    status = getattr(error, "status", None)
    if status:
        return str(status)
    payload = extract_gemini_error_payload(error)
    status = payload.get("status")
    return str(status) if status else None


def extract_gemini_error_message(error):
    message = getattr(error, "message", None)
    if message:
        return str(message)
    payload = extract_gemini_error_payload(error)
    message = payload.get("message") or payload.get("detail") or payload.get("error")
    if message:
        return str(message)
    text = str(error).strip()
    return text or None


def extract_gemini_error_payload(error):
    details = getattr(error, "details", None)
    if isinstance(details, dict):
        return details.get("error") if isinstance(details.get("error"), dict) else details

    response = getattr(error, "response", None)
    if response is not None:
        try:
            data = response.json()
            if isinstance(data, dict):
                return data.get("error") if isinstance(data.get("error"), dict) else data
        except Exception:
            pass
    return {}


def extract_gemini_request_id(error):
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if not headers:
        return None
    return (
        headers.get("x-request-id")
        or headers.get("X-Request-Id")
        or headers.get("x-goog-request-id")
        or headers.get("X-Goog-Request-Id")
    )


def _format_gemini_video_generation_error(error, model):
    message = str(error)
    return f"[{model.upper()}] {message}"
