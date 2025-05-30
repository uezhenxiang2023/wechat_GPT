import os, json, re

import lark_oapi as lark
from lark_oapi.api.im.v1 import *

from config import conf
from bridge.context import ContextType
from channel.chat_message import ChatMessage
from common.log import logger
from common.tmp_dir import TmpDir

def get_message_resource(*, message_id, file_key, type, file_path):
    """
    Use this method to get basic information about resource in IM and prepare it for downloading.
    """
    # 创建client
    client = lark.Client.builder() \
        .app_id(conf().get('feishu_app_id')) \
        .app_secret(conf().get('feishu_app_secret')) \
        .log_level(lark.LogLevel.INFO) \
        .build()

    # 构造请求对象
    request: GetMessageResourceRequest = GetMessageResourceRequest.builder() \
        .message_id(message_id) \
        .file_key(file_key) \
        .type(type) \
        .build()

    # 发起请求
    response: GetMessageResourceResponse = client.im.v1.message_resource.get(request)

    # 处理失败返回
    if not response.success():
        lark.logger.error(
            f"client.im.v1.message_resource.get failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}, resp: \n{json.dumps(json.loads(response.raw.content), indent=4, ensure_ascii=False)}")
        return

    # 处理业务结果
    f = open(file_path, "wb")
    f.write(response.file.read())
    f.close()

def get_file_name(file):
    file_path = file.file_path
    file_name_index = file_path.rfind("/")
    file_name = file_path[file_name_index+1:]
    return file_name


class FeishuMessage(ChatMessage):
    def __init__(self, event: dict, is_group=False):
        super().__init__(event)
        self.msg_id = event.message.message_id
        self.create_time = event.message.create_time
        self.is_group = is_group
        self.from_user_id = event.sender.sender_id.open_id
        self.to_user_id = event.message.chat_id
        self.other_user_id = self.to_user_id
        self.user_dir = TmpDir().path() + str(self.from_user_id) + '/request/'

        if event.message.message_type == 'text':
            self.ctype = ContextType.TEXT
            self.content = json.loads(event.message.content)["text"]
        elif event.message.message_type == 'audio':
            self.ctype = ContextType.VOICE
            self.content = TmpDir().path() + event.message["FileName"]  # content直接存临时目录路径
            self._prepare_fn = lambda: event.message.download(self.content)
        elif event.message.message_type == 'image':
            self.ctype = ContextType.IMAGE
            image_key = json.loads(event.message.content)["image_key"]
            self.content = self.user_dir + image_key  # content直接存临时目录路径
            self._prepare_fn = lambda: get_message_resource(message_id=self.msg_id, file_key=image_key, type=event.message.message_type, file_path=self.content)
        elif event.message.message_type == 'media':
            self.ctype = ContextType.VIDEO
            self.content = TmpDir().path() + event.message["FileName"]  # content直接存临时目录路径
            self._prepare_fn = lambda: event.message.download(self.content)
        elif event.message.message_type == 'file':
            self.ctype = ContextType.FILE
            file_key = json.loads(event.message.content)["file_key"]
            file_name = json.loads(event.message.content)["file_name"]
            self.content = self.user_dir + file_name  # content直接存临时目录路径
            self._prepare_fn = lambda: get_message_resource(message_id=self.msg_id, file_key=file_key, type=event.message.message_type, file_path=self.content)
        else:
            raise NotImplementedError("Unsupported message type: Type:{} MsgType:{}".format(event.message["Type"], event.message["MsgType"]))