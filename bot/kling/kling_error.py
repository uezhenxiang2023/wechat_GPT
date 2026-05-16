import requests


KLING_SUCCESS_CODES = {0}
KLING_RETRYABLE_CODES = {1303}

KLING_ERROR_MESSAGES = {
    1000: "身份验证失败，请检查 Authorization 是否正确。",
    1001: "Authorization 为空，请在请求头中填写正确的 Authorization。",
    1002: "Authorization 值非法，请检查 AK/SK 配置。",
    1003: "Authorization 未到有效时间，请等待生效或重新签发 Token。",
    1004: "Authorization 已失效，请重新签发 Token。",
    1100: "账户异常，请检查账户配置信息。",
    1101: "账户欠费，请充值确保余额充足。",
    1102: "资源包已用完或过期，请购买额外资源包或开通后付费。",
    1103: "账户无权限访问该接口或模型，请检查账户权限。",
    1200: "请求参数非法，请检查请求参数是否正确。",
    1201: "参数非法：{message}",
    1202: "请求 method 无效，请查看接口文档使用正确的 method。",
    1203: "请求的资源不存在：{message}",
    1300: "触发平台策略，请检查是否触发平台策略。",
    1301: "内容触发安全策略，请修改提示词或参考素材后重试。",
    1302: "请求过快，超过速率限制，请降低频率或稍后重试。",
    1303: "并发超限，请稍后重试。",
    1304: "触发 IP 白名单策略，请联系客服。",
    5000: "服务器内部错误，请稍后重试。",
    5001: "服务器暂时不可用（维护中），请稍后重试。",
    5002: "服务器内部超时，请稍后重试。",
}


def is_kling_success(data):
    return extract_kling_code(data) in KLING_SUCCESS_CODES


def is_kling_retryable(data):
    return extract_kling_code(data) in KLING_RETRYABLE_CODES


def format_kling_response_error(data, *, service_name="可灵"):
    code = extract_kling_code(data)
    if code in KLING_SUCCESS_CODES or code in KLING_RETRYABLE_CODES:
        return None

    message = extract_kling_message(data)
    template = KLING_ERROR_MESSAGES.get(code)
    if template:
        detail = template.format(message=message or "")
        if "{message}" not in template and message:
            detail = f"{detail}（{message}）"
    else:
        detail = f"未知错误：{message}" if message else "未知错误"
    return f"[{code}] {service_name}：{detail}"


def format_kling_task_failure(data, *, service_name="可灵"):
    payload = data.get("data", {}) if isinstance(data, dict) else {}
    message = payload.get("task_status_msg") or payload.get("task_status_message") or extract_kling_message(data)
    return f"{service_name}任务失败：{message or '未返回失败原因'}"


def format_kling_http_error(error, *, service_name="可灵"):
    if isinstance(error, requests.Timeout):
        return f"{service_name}请求超时，请稍后重试。"
    if isinstance(error, requests.ConnectionError):
        return f"无法连接{service_name}服务，请检查网络或稍后重试。"
    if isinstance(error, requests.HTTPError):
        response = getattr(error, "response", None)
        status_code = getattr(response, "status_code", None)
        data = _safe_response_json(response)
        business_error = format_kling_response_error(data, service_name=service_name) if data else None
        if business_error:
            return business_error
        return f"{service_name}请求失败(status={status_code})，请稍后重试。"
    if isinstance(error, requests.RequestException):
        return f"{service_name}请求异常：{error}"
    return None


def extract_kling_code(data):
    if not isinstance(data, dict):
        return None
    code = data.get("code", 0)
    try:
        return int(code)
    except (TypeError, ValueError):
        return code


def extract_kling_message(data):
    if not isinstance(data, dict):
        return ""
    message = data.get("message") or data.get("msg") or ""
    return str(message) if message is not None else ""


def _safe_response_json(response):
    if response is None:
        return None
    try:
        return response.json()
    except ValueError:
        return None
