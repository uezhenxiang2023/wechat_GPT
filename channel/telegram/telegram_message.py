import re

from bridge.context import ContextType
from channel.chat_message import ChatMessage
from common.log import logger
from common.tmp_dir import TmpDir
from telegram.ext import Updater
from config import conf

def get_file(file_id):
        """
        Use this method to get basic information about a file and prepare it for downloading.
        """
        bot_token = conf().get("telegram_bot_token")
        proxy_url = conf().get("telegram_proxy_url")
        updater = Updater(bot_token, request_kwargs={'proxy_url': proxy_url})
        bot = updater.bot
        try:
            file = bot.get_file(file_id)
            return file, None
        except Exception as e:
            return None, e

def get_file_name(file):
    file_path = file.file_path
    file_name_index = file_path.rfind("/")
    file_name = file_path[file_name_index+1:]
    return file_name

class TelegramMessage(ChatMessage):
    def __init__(self, telegram_message, is_group=False):
        super().__init__(telegram_message)
        self.msg_id = telegram_message["message_id"]
        self.create_time = telegram_message["date"]
        self.is_group = is_group
        self.from_user_id = telegram_message["from_user"]["id"]
        self.to_user_id = telegram_message["chat_id"]
        self.other_user_id = self.to_user_id

        if telegram_message["text"]:
            self.ctype = ContextType.TEXT
            self.content = telegram_message["text"]
        elif telegram_message["voice"]:
            self.ctype = ContextType.VOICE
            self.content = TmpDir().path() + telegram_message["FileName"]  # content直接存临时目录路径
            self._prepare_fn = lambda: telegram_message.download(self.content)
        elif telegram_message["photo"]:
            self.ctype = ContextType.IMAGE
            file_id = telegram_message.photo[3].file_id
            file, error = get_file(file_id)
            if file:
                self.content = TmpDir().path() + get_file_name(file)  # content直接存临时目录路径
                self._prepare_fn = lambda: file.download(self.content)
            if error:
                error_reply = f"[TELEGRAMBOT] fetch get_file() error '{error}' ,because <{file_id}> is larger than 20MB, can't be downloaded" 
                logger.error(error_reply)
                raise NotImplementedError(error_reply)
        elif telegram_message["video"]:
            self.ctype = ContextType.VIDEO
            self.content = TmpDir().path() + telegram_message["FileName"]  # content直接存临时目录路径
            self._prepare_fn = lambda: telegram_message.download(self.content)
        elif telegram_message["NOTE"] and telegram_message["MsgType"] == 10000:
            if is_group and ("加入群聊" in telegram_message["Content"] or "加入了群聊" in telegram_message["Content"]):
                # 这里只能得到nickname， actual_user_id还是机器人的id
                if "加入了群聊" in telegram_message["Content"]:
                    self.ctype = ContextType.JOIN_GROUP
                    self.content = telegram_message["Content"]
                    self.actual_user_nickname = re.findall(r"\"(.*?)\"", telegram_message["Content"])[-1]
                elif "加入群聊" in telegram_message["Content"]:
                    self.ctype = ContextType.JOIN_GROUP
                    self.content = telegram_message["Content"]
                    self.actual_user_nickname = re.findall(r"\"(.*?)\"", telegram_message["Content"])[0]

            elif is_group and ("移出了群聊" in telegram_message["Content"]):
                self.ctype = ContextType.EXIT_GROUP
                self.content = telegram_message["Content"]
                self.actual_user_nickname = re.findall(r"\"(.*?)\"", telegram_message["Content"])[0]
                    
            elif "你已添加了" in telegram_message["Content"]:  #通过好友请求
                self.ctype = ContextType.ACCEPT_FRIEND
                self.content = telegram_message["Content"]
            elif "拍了拍我" in telegram_message["Content"]:
                self.ctype = ContextType.PATPAT
                self.content = telegram_message["Content"]
                if is_group:
                    self.actual_user_nickname = re.findall(r"\"(.*?)\"", telegram_message["Content"])[0]
            else:
                raise NotImplementedError("Unsupported note message: " + telegram_message["Content"])
        elif telegram_message["document"]:
            self.ctype = ContextType.FILE
            file_id = telegram_message.document.file_id
            file_name = telegram_message.document.file_name
            file, error = get_file(file_id)
            if file:
                self.content = TmpDir().path() + file_name  # content直接存临时目录路径
                self._prepare_fn = lambda: file.download(self.content)
            elif error:
                error_reply = f"[TELEGRAMBOT] fetch get_file() error '{error}' ,because <{file_name}> is larger than 20MB, can't be downloaded" 
                logger.error(error_reply)
                raise NotImplementedError(error_reply)
        elif telegram_message["SHARING"]:
            self.ctype = ContextType.SHARING
            self.content = telegram_message.get("Url")

        else:
            raise NotImplementedError("Unsupported message type: Type:{} MsgType:{}".format(telegram_message["Type"], telegram_message["MsgType"]))

        """user_id = itchat.instance.storageClass.userName
        nickname = itchat.instance.storageClass.nickName

        # 虽然from_user_id和to_user_id用的少，但是为了保持一致性，还是要填充一下
        # 以下很繁琐，一句话总结：能填的都填了。
        if self.from_user_id == user_id:
            self.from_user_nickname = nickname
        if self.to_user_id == user_id:
            self.to_user_nickname = nickname
        try:  # 陌生人时候, User字段可能不存在
            # my_msg 为True是表示是自己发送的消息
            self.my_msg = telegram_message["ToUserName"] == telegram_message["User"]["UserName"] and \
                          telegram_message["ToUserName"] != telegram_message["FromUserName"]
            self.other_user_id = telegram_message["User"]["UserName"]
            self.other_user_nickname = telegram_message["User"]["NickName"]
            if self.other_user_id == self.from_user_id:
                self.from_user_nickname = self.other_user_nickname
            if self.other_user_id == self.to_user_id:
                self.to_user_nickname = self.other_user_nickname
            if telegram_message["User"].get("Self"):
                # 自身的展示名，当设置了群昵称时，该字段表示群昵称
                self.self_display_name = telegram_message["User"].get("Self").get("DisplayName")
        except KeyError as e:  # 处理偶尔没有对方信息的情况
            logger.warn("[WX]get other_user_id failed: " + str(e))
            if self.from_user_id == user_id:
                self.other_user_id = self.to_user_id
            else:
                self.other_user_id = self.from_user_id

        if self.is_group:
            self.is_at = telegram_message["IsAt"]
            self.actual_user_id = telegram_message["ActualUserName"]
            if self.ctype not in [ContextType.JOIN_GROUP, ContextType.PATPAT, ContextType.EXIT_GROUP]:
                self.actual_user_nickname = telegram_message["ActualNickName"]"""
