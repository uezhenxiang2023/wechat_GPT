import io
import requests
import logging

from io import BytesIO

from bridge.context import ContextType, Context
from bridge.reply import Reply, ReplyType
from channel.chat_channel import ChatChannel
from channel.chat_message import ChatMessage
from channel.telegram.telegram_message import TelegramMessage
from common import const, tool_button
from common.log import logger
from common.singleton import singleton
from config import conf
from channel.telegram.telegram_text_util import escape

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ParseMode
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, CallbackQueryHandler

@singleton
class TelegramChannel(ChatChannel):
    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.bot_token = conf().get("telegram_bot_token")
        self.proxy_url = conf().get("telegram_proxy_url")

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

        # Print tool_button stasus to console
        logger.info(f'[TELEGRAMBOT-search] is {tool_button.searching},[TELEGRAMBOT-image] is {tool_button.imaging}')

        self.handler_single_msg(update.message)

    def search(self, update: Update, context: CallbackContext) -> None:
        """
        This function handles the /search command
        """

        if tool_button.searching:
            text = "联网功能已关闭，如果需要，可以通过消息输入框左侧的命令菜单随时开启。"
        elif not tool_button.searching:
            text = "联网搜索功能已开启，需要我帮你查询点啥？"

        text = escape(text)
        tool_button.searching = not tool_button.searching
        
        """title = 'mykhel-AC.com'
        #title = self.escape(title)
        uri = 'https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUBnsYuu47exMpwknPadJIxKH7qObA_WQIjY9ZYeyjBh2PQTdJD1GVXgO_ZJdrszG1TmKsxbwx__I0MclAhpbS2j3PrR9p0Agvl0GePubSqXla0TqRh2ScfiiCmMsOD3Hu08mw2nPg6FeY3TyiZk4CPrImU1dOOaDZorxZwh5ikJYslIsVLm7Un0ZE6Q1gj69u0mqHWcQdyX'
        inline_url = f'1\.[{title}]({uri})\n2\.[{title}]({uri})'
        # '~'for strikethrough, '_' for italic, '*' for bold, '__' for underline
        text_marddown = f'{text}\n\n{inline_url}'"""

        context.bot.send_message(
            update.message.chat_id,
            text,
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=False
            # To preserve the markdown, we attach entities (bold, italic...)
            #entities=update.message.entities
        )
        logger.info(f'[TELEGRAMBOT]{text}')

    def image(self, update: Update, context: CallbackContext) -> None:
        """
        This function handles /image command
        """

        if tool_button.imaging:
            text = "图片生成功能已关闭，如果需要，可以通过消息输入框左侧的命令菜单随时开启。"
        elif not tool_button.imaging:
            text = "图片生成功能已开启，需要我帮你弄点啥图？"

        text = escape(text)
        tool_button.imaging = not tool_button.imaging

        context.bot.send_message(
            update.message.chat_id,
            text,
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=False
        )
        logger.info(f'[TELEGRAMBOT_{const.GEMINI_2_FLASH_IMAGE_GENERATION}]{text}')


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
        dispatcher.add_handler(CommandHandler("search", self.search))
        dispatcher.add_handler(CommandHandler("image", self.image))
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
            try:
                self.send_text(escape(reply.content), receiver)
                logger.info("[TELEGRAMBOT] sendMsg={}, receiver={}".format(reply, receiver))
            except Exception as e:
                logger.error("[TELEGRAMBOT] sendMsg error, reply={}, receiver={}, error={}".format(reply, receiver, e))
                self.send_text(const.ERROR_RESPONSE, toUserName=receiver)
                logger.info("[TELEGRAMBOT] sendMsg={}, receiver={}".format(reply.content, receiver))
        elif reply.type == ReplyType.ERROR:
            self.send_text(const.ERROR_RESPONSE, toUserName=receiver)
            logger.info("[TELEGRAMBOT] sendMsg={}, receiver={}".format(reply.content, receiver))
        elif reply.type == ReplyType.INFO:
            self.send_text(escape(reply.content), toUserName=receiver)
            logger.info("[TELEGRAMBOT] sendMsg={}, receiver={}".format(reply, receiver))
        elif reply.type == ReplyType.VOICE:
            self.send_file(reply.content, toUserName=receiver)
            logger.info("[TELEGRAMBOT] sendFile={}, receiver={}".format(reply.content, receiver))
        elif reply.type == ReplyType.IMAGE_URL:  # 获取网络资源
            response = reply.content
            if not isinstance(response, str):
                #获取网址
                parts = response.candidates[0].content.parts
                grouding_metadata = response.candidates[0].grounding_metadata
                if parts is None:
                    finish_reason = response.candidates[0].finish_reason
                    logger.error("[TELEGRAMBOT] sendMsg error, reply={}, receiver={}, error={}".format(reply, receiver, finish_reason))
                    self.send_text(const.ERROR_RESPONSE, toUserName=receiver)
                    logger.info("[TELEGRAMBOT] sendMsg={}, receiver={}".format(reply.content, receiver))
                elif parts is not None:
                    reply_text = escape("\n".join(part.text for part in parts))
                if grouding_metadata is not None:
                    inline_url = self.get_search_sources(grouding_metadata)
                reply_content = reply_text + "\n\n" + inline_url
                self.send_text(reply_content, receiver)
                logger.info("[TELEGRAMBOT_{}] sendMsg={}, receiver={}".format(const.GEMINI_2_FLASH_IMAGE_GENERATION, reply_content, receiver))

            else:
                # 下载图片
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
            response = reply.content
            parts = response.candidates[0].content.parts
            if parts is None:
                finish_reason = response.candidates[0].finish_reason
                logger.error("[TELEGRAMBOT] sendMsg error, reply={}, receiver={}, error={}".format(reply, receiver, finish_reason))
                self.send_text(const.ERROR_RESPONSE, toUserName=receiver)
                logger.info("[TELEGRAMBOT] sendMsg={}, receiver={}".format(reply.content, receiver))
            else:
                for part in parts:
                    if part.text:
                        reply_text = escape(part.text)
                        self.send_text(reply_text, receiver)
                        logger.info("[TELEGRAMBOT_{}] sendMsg={}, receiver={}".format(const.GEMINI_2_FLASH_IMAGE_GENERATION, part.text, receiver))
                    elif part.inline_data:
                        image = BytesIO(part.inline_data.data)
                        logger.info(f"[TELEGRAMBOT_{const.GEMINI_2_FLASH_IMAGE_GENERATION}] reply={image}")
                        image.seek(0)
                        self.send_image(image, receiver)
                        logger.info("[TELEGRAMBOT_{}] sendMsg={}, receiver={}".format(const.GEMINI_2_FLASH_IMAGE_GENERATION, image, receiver))
        elif reply.type == ReplyType.FILE:  # 新增文件回复类型
            file_pathes = reply.content['function_response']['file_pathes']
            reply_text = escape(reply.content['reply_text'])
            for file_path in file_pathes:
                with open(file_path, "rb") as f:
                    self.send_file(f, toUserName=receiver)
                logger.info("[TELEGRAMBOT] sendFile={}, receiver={}".format(file_path, receiver))
            self.send_text(reply_text, receiver)
            logger.info("[TELEGRAMBOT] sendMsg={}, receiver={}".format(reply_text, receiver))
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

    def get_search_sources(self, grounding_metadata):
        """
        Get search sources from the response Grounded with Google Search
        """
        sources = []
        ground_chunks = grounding_metadata.grounding_chunks
        for i, ground_chunk in enumerate(ground_chunks):
            title = ground_chunk.web.title
            title = escape(title)
            uri = ground_chunk.web.uri
            source = f'{i+1}\.[{title}]({uri})'
            sources.append(source)
        inline_url = '\n'.join(sources)
        return inline_url

    def send_image(self, reply_content, toUserName):
        """
        This function sends a response image back to the user.
        """
        updater = Updater(self.bot_token, request_kwargs={'proxy_url': self.proxy_url})
        bot = updater.bot
        bot.send_photo(chat_id=toUserName, photo=reply_content)

    def send_file(self, reply_content, toUserName):
        """
        This function sends a response file back to the user.
        """
        updater = Updater(self.bot_token, request_kwargs={'proxy_url': self.proxy_url})
        bot = updater.bot
        bot.send_document(chat_id=toUserName, document=reply_content)