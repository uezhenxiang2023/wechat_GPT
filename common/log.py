import logging
import sys


def _reset_logger(log):
    for handler in log.handlers:
        handler.close()
        log.removeHandler(handler)
        del handler
    log.handlers.clear()
    log.propagate = False
    console_handle = logging.StreamHandler(sys.stdout)
    # 控制台 handler 设置为 INFO 级别
    console_handle.setLevel(logging.INFO)
    console_handle.setFormatter(
        logging.Formatter(
            "[%(levelname)s][%(asctime)s][%(filename)s:%(lineno)d] - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    file_handle = logging.FileHandler("run.log", encoding="utf-8")
    # 文件 handler 设置为 DEBUG 级别
    file_handle.setLevel(logging.DEBUG)
    file_handle.setFormatter(
        logging.Formatter(
            "[%(levelname)s][%(asctime)s][%(filename)s:%(lineno)d] - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    log.addHandler(file_handle)
    log.addHandler(console_handle)


def _get_logger():
    log = logging.getLogger("log")
    _reset_logger(log)
    log.setLevel(logging.DEBUG)
    return log


# 日志句柄
logger = _get_logger()