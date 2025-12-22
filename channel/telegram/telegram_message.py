import re
import asyncio
from bridge.context import ContextType
from channel.chat_message import ChatMessage
from common.log import logger
from common.tmp_dir import TmpDir
from config import conf


def get_file(file_id):
        """
        Use this method to get basic information about a file and prepare it for downloading.
        """
        """bot_token = conf().get("telegram_bot_token")
        proxy_url = conf().get("telegram_proxy_url")
        updater = Updater(bot_token, request_kwargs={'proxy_url': proxy_url})
        bot = updater.bot
        try:
            file = bot.get_file(file_id)
            return file, None
        except Exception as e:
            return None, e"""


def get_file_name(file):
    file_path = file.file_path
    file_name_index = file_path.rfind("/")
    file_name = file_path[file_name_index+1:]
    return file_name


class TelegramMessage(ChatMessage):
    def __init__(self, telegram_message, is_group=False, main_loop=None):
        """
        TelegramMessage类的构建函数
        
        :param telegram_message: PTB 的 Message 对象
        :param is_group: 是否群聊
        :param main_loop: 【关键】主线程的事件循环，用于执行异步操作
        """
        super().__init__(telegram_message)

        # 1. 保存 Loop 和 Bot 引用
        self.loop = main_loop
        # 在 PTB v20 中，message 对象知道自己的 bot 是谁
        self.bot = telegram_message._bot

        self.msg_id = telegram_message["message_id"]
        self.create_time = telegram_message["date"]
        self.is_group = is_group
        self.from_user_id = telegram_message["from_user"]["id"]
        self.to_user_id = telegram_message["chat_id"]
        self.other_user_id = self.to_user_id
        self.user_dir = TmpDir().path() + str(self.from_user_id) + '/request/'

        def _run_sync(coro):
            """
            在同步环境执行异步代码,利用 run_coroutine_threadsafe 把任务发回主线程，并阻塞等待结果
            
            :param coro: PTB定义的异步方法
            """
            if self.loop:
                future = asyncio.run_coroutine_threadsafe(coro, self.loop)
                return future.result() # 阻塞直到拿到结果
            return None

        if telegram_message["text"]:
            self.ctype = ContextType.TEXT
            self.content = telegram_message["text"]
        elif telegram_message["voice"]:
            self.ctype = ContextType.VOICE
            # 获取 file_id
            file_id = telegram_message.voice.file_id
            # 【异步转同步】获取文件信息对象
            new_file = _run_sync(self.bot.get_file(file_id))
            
            # v20 的 file_path 可能需要处理一下文件名
            if new_file and new_file.file_path:
                file_name = new_file.file_path.split('/')[-1]
            else:
                file_name = f"{file_id}.ogg"
                
            self.content = self.user_dir + file_name
            # 定义下载函数：注意 download_to_drive 也是异步的
            self._prepare_fn = lambda: _run_sync(new_file.download_to_drive(self.content))
        elif telegram_message["photo"]:
            self.ctype = ContextType.IMAGE
            # 取最大分辨率的图
            photo_obj = telegram_message.photo[-1]
            file_id = photo_obj.file_id
            try:
                # 【异步转同步】调用 get_file
                new_file = _run_sync(self.bot.get_file(file_id))
                
                if new_file:
                    file_name = new_file.file_path.split('/')[-1]
                    self.content = self.user_dir + file_name
                    # 【异步转同步】下载
                    self._prepare_fn = lambda: _run_sync(new_file.download_to_drive(self.content))
                else:
                    raise Exception("get_file return None")
                    
            except Exception as e:
                error_reply = f"[TELEGRAMBOT] Image download failed: {e}"
                logger.error(error_reply)
                raise NotImplementedError(error_reply)
            
        elif telegram_message["video"]:
            self.ctype = ContextType.VIDEO
            file_id = telegram_message.video.file_id
            file_name = telegram_message.video.file_name or f"{file_id}.mp4"
            new_file = _run_sync(self.bot.get_file(file_id))
            self.content = self.user_dir + file_name
            self._prepare_fn = lambda: _run_sync(new_file.download_to_drive(self.content))
        elif telegram_message.new_chat_members:
            self.ctype = ContextType.JOIN_GROUP
            # 获取加入者的名字
            users = ", ".join([u.full_name for u in telegram_message.new_chat_members])
            self.content = f"{users} 加入了群聊"
            self.actual_user_nickname = users
        elif telegram_message.left_chat_member:
            self.ctype = ContextType.EXIT_GROUP
            user = telegram_message.left_chat_member.full_name
            self.content = f"{user} 移出了群聊"
            self.actual_user_nickname = user
        elif telegram_message["document"]:
            self.ctype = ContextType.FILE
            file_id = telegram_message.document.file_id
            file_name = telegram_message.document.file_name
            
            try:
                new_file = _run_sync(self.bot.get_file(file_id))
                if new_file:
                    self.content = self.user_dir + file_name
                    self._prepare_fn = lambda: _run_sync(new_file.download_to_drive(self.content))
                else:
                    raise Exception("get_file failed")
            except Exception as e:
                logger.error(f"[TELEGRAMBOT] File download error: {e}")
                raise NotImplementedError(str(e))
        elif 'https://' in telegram_message["text_html"]:
            self.ctype = ContextType.SHARING
            self.content = telegram_message["text_html"]

        else:
            # 暂时忽略其他不支持的类型（如 Sticker, Location）
                logger.debug(f"[TELEGRAM] Unsupported message type: {telegram_message}")
                raise NotImplementedError("Unsupported message type")

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
