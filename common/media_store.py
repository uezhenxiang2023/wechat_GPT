import os
from urllib.parse import quote

from common.log import logger
from common.tmp_dir import TmpDir
from config import conf


_PUBLIC_URL_CACHE = {}


def build_public_media_url(file_path):
    provider = str(conf().get("media_store_provider", "local") or "local").strip().lower()
    if provider == "tos":
        return _build_tos_public_url(file_path)
    return _build_local_public_url(file_path)


def _build_local_public_url(file_path):
    base_url = str(conf().get("media_public_base_url", "") or "").rstrip("/")
    if not base_url:
        return None
    relative_path = _get_tmp_relative_path(file_path)
    if relative_path is None:
        return None
    return f"{base_url}/tmp_media/{quote(relative_path, safe='/')}"


def _build_tos_public_url(file_path):
    cache_key = _build_cache_key(file_path, "tos")
    cached_url = _PUBLIC_URL_CACHE.get(cache_key)
    if cached_url:
        return cached_url

    public_base_url = str(conf().get("tos_public_base_url", "") or "").rstrip("/")
    bucket = str(conf().get("tos_bucket", "") or "").strip()
    endpoint = str(conf().get("tos_endpoint", "") or "").strip()
    access_key = str(conf().get("tos_access_key", "") or "").strip()
    secret_key = str(conf().get("tos_secret_key", "") or "").strip()
    object_key = _build_tos_object_key(file_path)
    if object_key is None:
        return None

    missing_config = []
    if not public_base_url:
        missing_config.append("tos_public_base_url")
    if not bucket:
        missing_config.append("tos_bucket")
    if not endpoint:
        missing_config.append("tos_endpoint")
    if not access_key:
        missing_config.append("tos_access_key")
    if not secret_key:
        missing_config.append("tos_secret_key")
    if missing_config:
        logger.warning(
            "[MediaStore] media_store_provider=tos but missing config: %s",
            ", ".join(missing_config),
        )
        return None

    if not _upload_file_to_tos(file_path, object_key, endpoint, access_key, secret_key):
        return None

    public_url = f"{public_base_url}/{quote(object_key, safe='/')}"
    _PUBLIC_URL_CACHE[cache_key] = public_url
    return public_url


def _upload_file_to_tos(file_path, object_key, endpoint, access_key, secret_key):
    try:
        import tos  # type: ignore
    except ImportError:
        logger.warning(
            "[MediaStore] media_store_provider=tos but Python package `tos` is not installed. "
            "Please install the TOS SDK before enabling this mode."
        )
        return False

    region = str(conf().get("tos_region", "") or "").strip()
    bucket = str(conf().get("tos_bucket", "") or "").strip()
    if not bucket:
        logger.warning("[MediaStore] missing tos_bucket, skip TOS upload")
        return False

    try:
        client = tos.TosClientV2(access_key, secret_key, endpoint, region)
        client.put_object_from_file(bucket, object_key, file_path)
        logger.info("[MediaStore] uploaded media to TOS, bucket=%s, object_key=%s", bucket, object_key)
        return True
    except Exception as e:
        logger.warning("[MediaStore] upload to TOS failed, object_key=%s, error=%s", object_key, e)
        return False


def _build_tos_object_key(file_path):
    relative_path = _get_tmp_relative_path(file_path)
    if relative_path is None:
        return None
    prefix = str(conf().get("tos_prefix", "bigchao/tmp_media/") or "bigchao/tmp_media/").strip().strip("/")
    if prefix:
        return f"{prefix}/{relative_path}"
    return relative_path


def _get_tmp_relative_path(file_path):
    tmp_root = os.path.abspath(TmpDir().path())
    abs_path = os.path.abspath(file_path)
    if not abs_path.startswith(tmp_root + os.sep) and abs_path != tmp_root:
        logger.warning("[MediaStore] media path is outside tmp dir, skip public url build: %s", file_path)
        return None
    return os.path.relpath(abs_path, tmp_root).replace(os.sep, "/")


def _build_cache_key(file_path, provider):
    abs_path = os.path.abspath(file_path)
    try:
        stat_result = os.stat(abs_path)
        return (provider, abs_path, stat_result.st_size, stat_result.st_mtime_ns)
    except OSError:
        return (provider, abs_path)
