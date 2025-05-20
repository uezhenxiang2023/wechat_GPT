import os
import pathlib

from config import conf
from common.log import logger


class TmpDir(object):
    """A temporary directory that is deleted when the object is destroyed."""

    tmpFilePath = pathlib.Path("./tmp/")

    def __init__(self):
        pathExists = os.path.exists(self.tmpFilePath)
        if not pathExists:
            os.makedirs(self.tmpFilePath)

    def path(self):
        return str(self.tmpFilePath) + "/"
    
def create_user_dir(path):
    """创建用户私有目录"""
    user_path = pathlib.Path(path)
    os.makedirs(user_path)
    info = 'Dir is created:' + path
    logger.info(f'[{conf().get("channel_type").upper()}] {info}')
