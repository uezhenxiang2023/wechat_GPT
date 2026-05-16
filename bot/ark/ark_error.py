try:
    from volcenginesdkarkruntime._exceptions import (
        ArkAPIConnectionError,
        ArkAPIStatusError,
        ArkAPITimeoutError,
    )
except Exception:
    ArkAPIConnectionError = None
    ArkAPIStatusError = None
    ArkAPITimeoutError = None


ARK_ERROR_CODE_MESSAGES = {
    "AuthenticationError": "火山方舟 API 鉴权失败，请检查 API Key 配置。",
    "PermissionDenied": "火山方舟访问被拒绝，请检查账号权限、模型权限或 API Key 状态。",
    "AccessDenied": "火山方舟访问被拒绝，请检查账号权限、模型权限或 API Key 状态。",
    "ResourceNotFound": "火山方舟资源不存在，请检查模型 ID 或接口路径。",
    "ModelNotFound": "模型不存在，请检查当前模型配置。",
    "ModelNotOpen": "当前账号尚未开通该模型，请在火山方舟控制台开通后再试。",
    "InvalidParameter": "请求参数有误，请调整提示词、尺寸或参考素材参数后重试。",
    "MissingParameter": "请求缺少必要参数，请检查业务侧入参。",
    "RequestValidationError": "请求参数校验失败，请调整提示词、尺寸或参考素材参数后重试。",
    "InvalidImage": "参考图无效或无法读取，请更换图片后重试。",
    "ImageTooLarge": "参考图超过火山方舟限制，请压缩图片后重试。",
    "InvalidVideo": "参考视频无效或无法读取，请更换视频后重试。",
    "VideoTooLarge": "参考视频超过火山方舟限制，请压缩视频后重试。",
    "ContentFilter": "请求内容触发安全审核，请调整提示词或参考素材后重试。",
    "SensitiveContentDetected": "请求内容触发安全审核，请调整提示词或参考素材后重试。",
    "QuotaExceeded": "火山方舟账户额度不足，请充值或调整配额后再试。",
    "RateLimitExceeded": "火山方舟当前请求过多，请稍后重试。",
    "ServiceUnavailable": "火山方舟服务暂时不可用，请稍后重试。",
    "InternalServiceError": "火山方舟服务内部错误，请稍后重试。",
}

ARK_STATUS_MESSAGES = {
    400: "请求参数有误，请调整提示词、尺寸或参考素材参数后重试。",
    401: "火山方舟 API 鉴权失败，请检查 API Key 配置。",
    403: "火山方舟访问被拒绝，请检查账号权限、模型权限或 API Key 状态。",
    404: "火山方舟资源不存在，请检查模型 ID 或接口路径。",
    409: "火山方舟请求冲突，请稍后重试。",
    422: "请求无法处理，请检查参考素材是否有效、可读取，或调整参数组合。",
    429: "火山方舟当前请求过多，请稍后重试。",
    500: "火山方舟服务内部错误，请稍后重试。",
    502: "火山方舟上游服务暂时不可用，请稍后重试。",
    503: "火山方舟服务繁忙或不可用，请稍后重试。",
    504: "火山方舟服务响应超时，请稍后重试。",
}


def is_ark_status_error(error):
    return ArkAPIStatusError is not None and isinstance(error, ArkAPIStatusError)


def is_ark_timeout_error(error):
    return ArkAPITimeoutError is not None and isinstance(error, ArkAPITimeoutError)


def is_ark_connection_error(error):
    return ArkAPIConnectionError is not None and isinstance(error, ArkAPIConnectionError)


def format_ark_api_error(error, model, *, service_name="火山方舟"):
    status_code = extract_ark_status_code(error)
    error_code = extract_ark_error_code(error)
    message = extract_ark_error_message(error)
    request_id = extract_ark_request_id(error)

    code_text = f"，错误码={error_code}" if error_code else ""
    request_text = f" request_id={request_id}" if request_id else ""

    base_message = ARK_ERROR_CODE_MESSAGES.get(error_code)
    if not base_message:
        status_message = ARK_STATUS_MESSAGES.get(
            status_code,
            f"{service_name}请求失败(status={status_code})，请稍后重试。",
        )
        base_message = _prefix_service(status_message, service_name)

    if message:
        base_message = f"{base_message}（{message}）"
    return f"[{model.upper()}] {base_message}{code_text}{request_text}"


def format_ark_connection_error(error, model, *, error_type="connection"):
    request_id = getattr(error, "request_id", None)
    request_text = f" request_id={request_id}" if request_id else ""
    if error_type == "timeout":
        message = "火山方舟请求超时，请稍后重试。"
    else:
        message = "无法连接火山方舟服务，请检查网络或稍后重试。"
    return f"[{model.upper()}] {message}{request_text}"


def extract_ark_status_code(error):
    status_code = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)
    return status_code or "unknown"


def extract_ark_error_payload(error):
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        return body.get("error") if isinstance(body.get("error"), dict) else body

    response = getattr(error, "response", None)
    if response is not None:
        try:
            data = response.json()
            if isinstance(data, dict):
                return data.get("error") if isinstance(data.get("error"), dict) else data
        except Exception:
            pass
    return {}


def extract_ark_error_code(error):
    payload = extract_ark_error_payload(error)
    code = getattr(error, "code", None) or payload.get("code")
    return str(code) if code else None


def extract_ark_error_message(error):
    payload = extract_ark_error_payload(error)
    message = payload.get("message") or payload.get("msg") or getattr(error, "message", None)
    return str(message) if message else str(error).strip()


def extract_ark_request_id(error):
    request_id = getattr(error, "request_id", None)
    if request_id:
        return request_id
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if not headers:
        return None
    return (
        headers.get("x-request-id")
        or headers.get("X-Request-Id")
        or headers.get("x-tt-logid")
        or headers.get("X-Tt-Logid")
    )


def _prefix_service(message, service_name):
    if message.startswith("火山方舟") or message.startswith(service_name):
        return message
    return f"{service_name}{message}"
