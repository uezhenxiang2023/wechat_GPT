from bridge.context import ContextType
from channel.chat_message import ChatMessage
import json, re
from common.log import logger
from common.tmp_dir import TmpDir

def get_file(file_id):
        """
        Use this method to get basic information about a file and prepare it for downloading.
        """

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

        if event.message.message_type == 'text':
            self.ctype = ContextType.TEXT
            self.content = json.loads(event.message.content)["text"]
        elif event.message["voice"]:
            self.ctype = ContextType.VOICE
            self.content = TmpDir().path() + event.message["FileName"]  # content直接存临时目录路径
            self._prepare_fn = lambda: event.message.download(self.content)
        elif event.message["photo"]:
            self.ctype = ContextType.IMAGE
            file_id = event.message.photo[-1].file_id
            file, error = get_file(file_id)
            if file:
                self.content = TmpDir().path() + get_file_name(file)  # content直接存临时目录路径
                self._prepare_fn = lambda: file.download(self.content)
            if error:
                error_reply = f"[Lark] fetch get_file() error '{error}' ,because <{file_id}> is larger than 20MB, can't be downloaded" 
                logger.error(error_reply)
                raise NotImplementedError(error_reply)
        elif event.message["video"]:
            self.ctype = ContextType.VIDEO
            self.content = TmpDir().path() + event.message["FileName"]  # content直接存临时目录路径
            self._prepare_fn = lambda: event.message.download(self.content)
        elif event.message["NOTE"] and event.message["MsgType"] == 10000:
            if is_group and ("加入群聊" in event.message["Content"] or "加入了群聊" in event.message["Content"]):
                # 这里只能得到nickname， actual_user_id还是机器人的id
                if "加入了群聊" in event.message["Content"]:
                    self.ctype = ContextType.JOIN_GROUP
                    self.content = event.message["Content"]
                    self.actual_user_nickname = re.findall(r"\"(.*?)\"", event.message["Content"])[-1]
                elif "加入群聊" in event.message["Content"]:
                    self.ctype = ContextType.JOIN_GROUP
                    self.content = event.message["Content"]
                    self.actual_user_nickname = re.findall(r"\"(.*?)\"", event.message["Content"])[0]

            elif is_group and ("移出了群聊" in event.message["Content"]):
                self.ctype = ContextType.EXIT_GROUP
                self.content = event.message["Content"]
                self.actual_user_nickname = re.findall(r"\"(.*?)\"", event.message["Content"])[0]
                    
            elif "你已添加了" in event.message["Content"]:  #通过好友请求
                self.ctype = ContextType.ACCEPT_FRIEND
                self.content = event.message["Content"]
            elif "拍了拍我" in event.message["Content"]:
                self.ctype = ContextType.PATPAT
                self.content = event.message["Content"]
                if is_group:
                    self.actual_user_nickname = re.findall(r"\"(.*?)\"", event.message["Content"])[0]
            else:
                raise NotImplementedError("Unsupported note message: " + event.message["Content"])
        elif event.message["document"]:
            self.ctype = ContextType.FILE
            file_id = event.message.document.file_id
            file_name = event.message.document.file_name
            file, error = get_file(file_id)
            if file:
                self.content = TmpDir().path() + file_name  # content直接存临时目录路径
                self._prepare_fn = lambda: file.download(self.content)
            elif error:
                error_reply = f"[Lark] fetch get_file() error '{error}' ,because <{file_name}> is larger than 20MB, can't be downloaded" 
                logger.error(error_reply)
                raise NotImplementedError(error_reply)
        elif 'https://' in event.message["text_html"]:
            self.ctype = ContextType.SHARING
            self.content = event.message["text_html"]

        else:
            raise NotImplementedError("Unsupported message type: Type:{} MsgType:{}".format(event.message["Type"], event.message["MsgType"]))