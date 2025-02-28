import io
import requests
import logging
import json
import time
from common.log import logger

from bridge.context import ContextType, Context
from bridge.reply import Reply, ReplyType
from channel.chat_channel import ChatChannel
from channel.chat_message import ChatMessage
from channel.telegram.telegram_message import TelegramMessage
from common.singleton import singleton
from config import conf
import re

from telegram import Update, ForceReply, InlineKeyboardMarkup, InlineKeyboardButton, ParseMode
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, CallbackQueryHandler

def escape(text):
    """Escapes special characters for Telegram MarkdownV2."""
    escape_chars = r'\*_[]()~`>#+-=|{}.!'
    return re.sub(r'([' + re.escape(escape_chars) + r'])', r'\\\1', text)

@singleton
class TelegramChannel(ChatChannel):
    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.bot_token = conf().get("telegram_bot_token")
        self.proxy_url = conf().get("telegram_proxy_url")

        # Store bot screaming status
        self.screaming = False

        # Pre-assign menu text
        self.FIRST_MENU = "<b>Menu 1</b>\n\nA beautiful menu with a shiny inline button."
        self.SECOND_MENU = "<b>Menu 2</b>\n\nA better menu with even more shiny inline buttons."

        # Pre-assign button text
        self.NEXT_BUTTON = "Next"
        self.BACK_BUTTON = "Back"
        self.TUTORIAL_BUTTON = "Tutorial"

        # Build keyboards
        self.FIRST_MENU_MARKUP = InlineKeyboardMarkup([[
            InlineKeyboardButton(self.NEXT_BUTTON, callback_data=self.NEXT_BUTTON)
        ]])
        self.SECOND_MENU_MARKUP = InlineKeyboardMarkup([
            [InlineKeyboardButton(self.BACK_BUTTON, callback_data=self.BACK_BUTTON)],
            [InlineKeyboardButton(self.TUTORIAL_BUTTON, url="https://core.telegram.org/bots/api")]
        ])


    def echo(self, update: Update, context: CallbackContext) -> None:
        """
        This function would be added to the dispatcher as a handler for messages coming from the Bot API
        """

        # Print to console
        print(f'{update.message.from_user.first_name} wrote {update.message.text}\n[scream] is {self.screaming}')

        self.handler_single_msg(update.message)

    def scream(self, update: Update, context: CallbackContext) -> None:
        """
        This function handles the /scream command
        """

        self.screaming = True
        """text = "好的，我们来举个例子说明一下：\n\n" \
               "假设一家券商 A 借给盖茨 1000 股特斯拉股票进行做空，当时特斯拉的股价是 1000 美元/股。\n\n" \
               "1.  **借券利息/费用：**\n" \
               "    *   券商 A 可能会收取年化 2% 的借券利息。\n" \
               "    *   借券期限假设为 6 个月。\n" \
               "    *   那么，券商 A 收取的借券利息 = 1000 股 * 1000 美元/股 * 2% * (6/12) = 10000 美元。\n\n" \
               "2.  **交易佣金：**\n" \
               "    *   假设券商 A 收取的交易佣金为 0.1%。\n" \
               "    *   盖茨卖出 1000 股特斯拉股票，券商 A 收取的佣金 = 1000 股 * 1000 美元/股 * 0.1% = 1000 美元。\n" \
               "    *   假设特斯拉股价上涨到 1200 美元/股，盖茨买回 1000 股特斯拉股票，券商 A 收取的佣金 = 1000 股 * 1200 美元/股 * 0.1% = 1200 美元。\n" \
               "    *   总交易佣金 = 1000 美元 + 1200 美元 = 2200 美元。\n\n" \
               "3.  **提高交易活跃度：**\n" \
               "    *   做空交易增加了市场的交易量，吸引了更多的投资者参与。\n" \
               "    *   这可能导致券商 A 的整体交易量增加，从而带来更多的交易佣金收入。\n" \
               "    *   假设由于做空交易的带动，券商 A 的其他交易佣金收入增加了 5000 美元。\n\n" \
               "4.  **对冲风险：**\n" \
               "    *   假设券商 A 本身持有 5000 股特斯拉股票。\n" \
               "    *   由于盖茨做空特斯拉，如果特斯拉股价下跌，券商 A 可以通过做空交易来弥补一部分损失。\n" \
               "    *   假设特斯拉股价下跌了 100 美元/股，盖茨盈利 1000 股 * 100 美元/股 = 100000 美元。\n" \
               "    *   券商 A 由于持有 5000 股特斯拉股票，损失了 5000 股 * 100 美元/股 = 500000 美元。\n" \
               "    *   但是，由于盖茨做空特斯拉，券商 A 可以通过做空交易来弥补一部分损失。\n\n" \
               "**总结：**\n\n" \
               "在这个例子中，券商 A 通过借券利息、交易佣金、提高交易活跃度和对冲风险等方式，总共赚取了：\n\n" \
               "    *   借券利息：10000 美元\n" \
               "    *   交易佣金：2200 美元\n" \
               "    *   提高交易活跃度：5000 美元\n\n" \
               "总收入 = 10000 美元 + 2200 美元 + 5000 美元 = 17200 美元\n\n" \
               "当然，这只是一个简化的例子。实际情况可能更加复杂，券商的收入来源也可能更多。但是，这个例子可以帮助您更好地理解券商或机构如何从做空交易中获利。"
        text = escape(text)
        
        title = 'mykhel-AC.com'
        #title = self.escape(title)
        uri = 'https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUBnsYuu47exMpwknPadJIxKH7qObA_WQIjY9ZYeyjBh2PQTdJD1GVXgO_ZJdrszG1TmKsxbwx__I0MclAhpbS2j3PrR9p0Agvl0GePubSqXla0TqRh2ScfiiCmMsOD3Hu08mw2nPg6FeY3TyiZk4CPrImU1dOOaDZorxZwh5ikJYslIsVLm7Un0ZE6Q1gj69u0mqHWcQdyX'
        inline_url = f'1\.[{title}]({uri})\n2\.[{title}]({uri})'
        # '~'for strikethrough, '_' for italic, '*' for bold, '__' for underline
        text_marddown = f'{text}\n\n{inline_url}'

        context.bot.send_message(
            update.message.chat_id,
            text,
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True
            # To preserve the markdown, we attach entities (bold, italic...)
            #entities=update.message.entities
        )"""

    def whisper(self, update: Update, context: CallbackContext) -> None:
        """
        This function handles /whisper command
        """

        self.screaming = False


    def menu(self, update: Update, context: CallbackContext) -> None:
        """
        This handler sends a menu with the inline buttons we pre-assigned above
        """

        context.bot.send_message(
            update.message.from_user.id,
            self.FIRST_MENU,
            parse_mode=ParseMode.HTML,
            reply_markup=self.FIRST_MENU_MARKUP
        )


    def button_tap(self, update: Update, context: CallbackContext) -> None:
        """
        This handler processes the inline buttons on the menu
        """

        data = update.callback_query.data
        text = ''
        markup = None

        if data == self.NEXT_BUTTON:
            text = self.SECOND_MENU
            markup = self.SECOND_MENU_MARKUP
        elif data == self.BACK_BUTTON:
            text = self.FIRST_MENU
            markup = self.FIRST_MENU_MARKUP

        # Close the query to end the client-side loading animation
        update.callback_query.answer()

        # Update message content with corresponding menu section
        update.callback_query.message.edit_text(
            text,
            ParseMode.HTML,
            reply_markup=markup
        )


    def main(self) -> None:
        # Configure proxy settings
        updater = Updater(self.bot_token, request_kwargs={'proxy_url': self.proxy_url})

        # Get the dispatcher to register handlers
        # Then, we register each handler and the conditions the update must meet to trigger it
        dispatcher = updater.dispatcher

        # Register commands
        dispatcher.add_handler(CommandHandler("scream", self.scream))
        dispatcher.add_handler(CommandHandler("whisper", self.whisper))
        dispatcher.add_handler(CommandHandler("menu", self.menu))

        # Register handler for inline buttons
        dispatcher.add_handler(CallbackQueryHandler(self.button_tap))

        # Echo any message that is not a command
        dispatcher.add_handler(MessageHandler(~Filters.command, self.echo))

        # Start the Bot
        updater.start_polling()

        # Run the bot until you press Ctrl-C
        updater.idle()

    def startup(self):
        self.main()

    def handler_single_msg(self, msg):
        try:
            cmsg = TelegramMessage(msg, False)
        except NotImplementedError as e:
            logger.debug("[TELEGRAMBOT]single message {} skipped: {}".format(msg["MsgId"], e))
            error_reply = escape(e.args[0])
            self.send_text(error_reply, msg.chat_id)
            return None
        self.handle_single(cmsg)
        return None

    def handler_group_msg(self, msg):
        try:
            cmsg = TelegramMessage(msg, True)
        except NotImplementedError as e:
            logger.debug("[TELEGRAMBOT]group message {} skipped: {}".format(msg["MsgId"], e))
            return None
        self.handle_group(cmsg)
        return None
    
    def handle_single(self, cmsg: ChatMessage):
        # filter system message
        if cmsg.other_user_id in ["weixin"]:
            return
        if cmsg.ctype == ContextType.VOICE:
            if conf().get("speech_recognition") != True:
                return
            logger.debug("[TELEGRAMBOT]receive voice msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.IMAGE:
            logger.debug("[TELEGRAMBOT]receive image msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.VIDEO:
            logger.debug("[TELEGRAMBOT]receive video msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.SHARING:
            logger.debug("[TELEGRAMBOT]receive url msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.PATPAT:
            logger.debug("[TELEGRAMBOT]receive patpat msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.FILE:
            logger.debug("[TELEGRAMBOT]receive file msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.TEXT:
            logger.debug("[TELEGRAMBOT]receive text msg: {}, cmsg={}".format(cmsg.content, cmsg))
        else:
            logger.debug("[TELEGRAMBOT]receive msg: {}, cmsg={}".format(cmsg.content, cmsg))
        context = self._compose_context(cmsg.ctype, cmsg.content, isgroup=False, msg=cmsg)
        if context:
            self.produce(context)

    def handle_group(self, cmsg: ChatMessage):
        if cmsg.ctype == ContextType.VOICE:
            if conf().get("group_speech_recognition") != True:
                return
            logger.debug("[TELEGRAMBOT]receive voice for group msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.IMAGE:
            logger.debug("[TELEGRAMBOT]receive image for group msg: {}".format(cmsg.content))
        elif cmsg.ctype in [ContextType.JOIN_GROUP, ContextType.PATPAT, ContextType.ACCEPT_FRIEND, ContextType.EXIT_GROUP]:
            logger.debug("[TELEGRAMBOT]receive note msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.TEXT:
            logger.debug("[TELEGRAMBOT]receive group msg: {}, cmsg={}".format(cmsg.content, cmsg))
            pass
        elif cmsg.ctype == ContextType.FILE:
            logger.debug(f"[TELEGRAMBOT]receive attachment msg, file_name={cmsg.content}")
        else:
            logger.debug("[TELEGRAMBOT]receive group msg: {}".format(cmsg.content))
        context = self._compose_context(cmsg.ctype, cmsg.content, isgroup=True, msg=cmsg)
        if context:
            self.produce(context)

    # 统一的发送函数，每个Channel自行实现，根据reply的type字段发送不同类型的消息
    def send(self, reply: Reply, context: Context):
        receiver = context["receiver"]
        if reply.type == ReplyType.TEXT:
            self.send_text(reply.content, toUserName=receiver)
            logger.info("[TELEGRAMBOT] sendMsg={}, receiver={}".format(reply, receiver))
        elif reply.type == ReplyType.ERROR or reply.type == ReplyType.INFO:
            self.send_text(escape(reply.content), toUserName=receiver)
            logger.info("[TELEGRAMBOT] sendMsg={}, receiver={}".format(reply, receiver))
        elif reply.type == ReplyType.VOICE:
            self.send_file(reply.content, toUserName=receiver)
            logger.info("[TELEGRAMBOT] sendFile={}, receiver={}".format(reply.content, receiver))
        elif reply.type == ReplyType.IMAGE_URL:  # 从网络下载图片
            img_url = reply.content
            logger.debug(f"[TELEGRAMBOT] start download image, img_url={img_url}")
            pic_res = requests.get(img_url, stream=True)
            image_storage = io.BytesIO()
            size = 0
            for block in pic_res.iter_content(1024):
                size += len(block)
                image_storage.write(block)
            logger.info(f"[TELEGRAMBOT] download image success, size={size}, img_url={img_url}")
            image_storage.seek(0)
            self.send_image(image_storage, toUserName=receiver)
            logger.info("[TELEGRAMBOT] sendImage url={}, receiver={}".format(img_url, receiver))
        elif reply.type == ReplyType.IMAGE:  # 从文件读取图片
            image_storage = reply.content
            image_storage.seek(0)
            self.send_image(image_storage, toUserName=receiver)
            logger.info("[TELEGRAMBOT] sendImage, receiver={}".format(receiver))
        elif reply.type == ReplyType.FILE:  # 新增文件回复类型
            file_storage = reply.content
            self.send_file(file_storage, toUserName=receiver)
            logger.info("[TELEGRAMBOT] sendFile, receiver={}".format(receiver))
        elif reply.type == ReplyType.VIDEO:  # 新增视频回复类型
            video_storage = reply.content
            self.send_video(video_storage, toUserName=receiver)
            logger.info("[TELEGRAMBOT] sendFile, receiver={}".format(receiver))
        elif reply.type == ReplyType.VIDEO_URL:  # 新增视频URL回复类型
            video_url = reply.content
            logger.debug(f"[TELEGRAMBOT] start download video, video_url={video_url}")
            video_res = requests.get(video_url, stream=True)
            video_storage = io.BytesIO()
            size = 0
            for block in video_res.iter_content(1024):
                size += len(block)
                video_storage.write(block)
            logger.info(f"[TELEGRAMBOT] download video success, size={size}, video_url={video_url}")
            video_storage.seek(0)
            self.send_video(video_storage, toUserName=receiver)
            logger.info("[TELEGRAMBOT] sendVideo url={}, receiver={}".format(video_url, receiver))

    def send_text(self, reply_content, toUserName):
        """
        This function sends a response text message back to the user.
        """
        updater = Updater(self.bot_token, request_kwargs={'proxy_url': self.proxy_url})
        bot = updater.bot
        bot.send_message(chat_id=toUserName, text=reply_content, parse_mode=ParseMode.MARKDOWN_V2)