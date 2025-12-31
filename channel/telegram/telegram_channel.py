import io
import requests
import logging
import asyncio
import html

from io import BytesIO

from bridge.context import ContextType, Context
from bridge.reply import Reply, ReplyType
from channel.chat_channel import ChatChannel
from channel.chat_message import ChatMessage
from channel.telegram.telegram_message import TelegramMessage
from common import const
from common.tool_button import tool_state
from common.log import logger
from common.singleton import singleton
from config import conf
from channel.telegram.telegram_text_util import escape

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackContext, CallbackQueryHandler, Updater
from telegram.request import HTTPXRequest

@singleton
class TelegramChannel(ChatChannel):
    def __init__(self, session_id=None):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.bot_token = conf().get("telegram_bot_token")
        self.proxy_url =conf().get("telegram_proxy_url")


        # Pre-assign menu text
        self.FIRST_MENU = "<b>Menu 1</b>\n\nA beautiful menu with a shiny inline button."
        self.SECOND_MENU = "<b>Menu 2</b>\n\nA better menu with even more shiny inline buttons."

        # Pre-assign button text
        self.NEXT_BUTTON = "Next"
        self.BACK_BUTTON = "Back"
        self.TUTORIAL_BUTTON = "Tutorial"

        # Build keyboards
        self.FIRST_MENU_MARKUP = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(self.NEXT_BUTTON, callback_data=self.NEXT_BUTTON)]
            ]
        )
        self.SECOND_MENU_MARKUP = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(self.BACK_BUTTON, callback_data=self.BACK_BUTTON)],
                [InlineKeyboardButton(self.TUTORIAL_BUTTON, url="https://core.telegram.org/bots/api")]
            ]
        )

        # 新增：用于存储主事件循环的引用
        #self.main_loop = None


    async def echo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        This function would be added to the dispatcher as a handler for messages coming from the Bot API
        """

        chat_id = update.effective_chat.id
        # Print tool_button stasus to console
        logger.info(
            f'[TELEGRAMBOT-print] is {tool_state.get_print_state(chat_id)},\
            [TELEGRAMBOT-breakdown] is {tool_state.get_breakdown_state(chat_id)},\
            [TELEGRAMBOT-search] is {tool_state.get_search_state(chat_id)},\
            [TELEGRAMBOT-image] is {tool_state.get_image_state(chat_id)},\
            [TELEGRAMBOT-video] is {tool_state.get_breakdown_state(chat_id)},\
            requester={chat_id}')
        
        # 使用 run_in_executor 将同步的业务逻辑扔到子线程
        # 这样 handler_single_msg 里的耗时操作（如 GPT 请求）就不会卡死机器人
        loop = asyncio.get_running_loop()
        loop.run_in_executor(
            None, 
            self.handler_single_msg, 
            update.message
        )

    async def print(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        This function handles the /print command
        """
        chat_id = update.effective_chat.id
        if tool_state.get_print_state(chat_id):
            text = "[INFO]\n剧本排版功能已关闭，可以在消息框输入#print或/print命令，也可以点击输入框左侧菜单选择‘print’随时开启。"
        else:
            text = "[INFO]\n剧本排版功能已开启,请先上传pdf格式的剧本，再发我编剧姓名。\n我会按照好莱坞编剧工会的标准格式进行排版，让您的剧本看起来更专业、读起来更舒服，大大提升获得‘绿灯’的几率。"

        tool_state.toggle_printing(chat_id)

        await context.bot.send_message(
            chat_id=chat_id,
            text=text
        )
        logger.info(f'[TELEGRAMBOT]{text} requester={chat_id}')
    
    async def breakdown(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        This function handles the /breakdown command
        """
        chat_id = update.effective_chat.id
        if tool_state.get_breakdown_state(chat_id):
            text = "[INFO]\n拆解顺分场表功能已关闭，可以在消息框输入#breakdown或/breakdown命令，也可以点击输入框左侧菜单选择‘breakdown’,随时开启。"
        else:
            text = "[INFO]\n拆解顺分场表功能已开启。"

        tool_state.toggle_breakdowning(chat_id)

        await context.bot.send_message(
            chat_id=chat_id,
            text=text
        )
        logger.info(f'[TELEGRAMBOT]{text} requester={chat_id}')

    async def search(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        This function handles the /search command
        """
        chat_id = update.effective_chat.id
        if tool_state.get_search_state(chat_id):
            text = "[INFO]\n联网功能已关闭，如果需要，可以通过消息输入框左侧的命令菜单随时开启。"
        else:
            text = "[INFO]\n联网搜索功能已开启，需要我帮你查询点啥？"

        tool_state.toggle_searching(chat_id)
        await context.bot.send_message(
            chat_id=chat_id,
            text=text
        )
        logger.info(f'[TELEGRAMBOT]{text} requester={chat_id}')


    async def image(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        This function handles /image command
        """
        chat_id = update.effective_chat.id
        if tool_state.get_image_state(chat_id):
            text = "[INFO]\n图片生成功能已关闭，如果需要，可以通过消息输入框左侧的命令菜单随时开启。"
        else:
            text = "[INFO]\n图片生成功能已开启，需要我帮你弄点啥图？"

        tool_state.toggle_imaging(chat_id)

        await context.bot.send_message(
            chat_id=chat_id,
            text=text
        )
        logger.info(f'[TELEGRAMBOT_{conf().get('text_to_image')}]{text} requester={chat_id}')

    async def video(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        This function handles /video command
        """
        chat_id = update.effective_chat.id
        if tool_state.get_edit_state(chat_id):
            text = "[INFO]\n视频生成功能已关闭，如果需要，可以通过消息输入框左侧的命令菜单随时开启。"
        else:
            text = "[INFO]\n视频生成功能已开启，需要我帮你弄点啥视频？"

        tool_state.toggle_editing(chat_id)

        await context.bot.send_message(
            chat_id=chat_id,
            text=text
        )
        logger.info(f'[TELEGRAMBOT_{conf().get('text_to_video')}]{text} requester={chat_id}')


    async def menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        This handler sends a menu with the inline buttons we pre-assigned above
        """

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=self.FIRST_MENU,
            parse_mode='HTML',
            reply_markup=self.FIRST_MENU_MARKUP
        )


    async def button_tap(self, update: Update, context: CallbackContext) -> None:
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
        await update.callback_query.answer()

        # Update message content with corresponding menu section
        await update.callback_query.edit_message_text(
            text,
            parse_mode='HTML',
            reply_markup=markup
        )


    def main(self) -> None:
        """
        Start the bot.
        """
        # 准备 Request 对象
        request_params = {
            "connection_pool_size":1024, # 链接窗口数量
            "pool_timeout":120,          #链接排队时间
            "read_timeout":60,
            "write_timeout":60,
            "connect_timeout":60
        }
            
        if self.proxy_url:
            request_params["proxy"] = self.proxy_url
            logger.info(f"[TELEGRAM] 使用代理: {self.proxy_url}")

        
        request_instance = HTTPXRequest(**request_params)

        # Create the Application and pass it your bot's token.
        self.application = (
            Application.builder()
            .token(self.bot_token)
            .request(request_instance)
            .build()
        )

        # Register commands
        self.application.add_handler(CommandHandler("print", self.print))
        self.application.add_handler(CommandHandler("breakdown", self.breakdown))
        self.application.add_handler(CommandHandler("search", self.search))
        self.application.add_handler(CommandHandler("image", self.image))
        self.application.add_handler(CommandHandler("video", self.video))
        self.application.add_handler(CommandHandler("menu", self.menu))

        # Register handler for inline buttons
        self.application.add_handler(CallbackQueryHandler(self.button_tap))

        # Echo any message that is not a command
        filter_rules = (
            filters.TEXT | 
            filters.PHOTO | 
            filters.Document.ALL | 
            filters.VIDEO | 
            filters.VOICE |
            filters.StatusUpdate.ALL  # 如果您还需要处理进群/退群通知，加上这个
        ) & (~filters.COMMAND)
        self.application.add_handler(MessageHandler(filter_rules, self.echo))

        logger.info("[TELEGRAMBOT] Bot 正在启动...")

        # 在启动 Polling 之前，保存当前的事件循环
        # 注意：这里需要确保 loop 已经存在。
        # 如果是 script 运行，通常需要手动 get_event_loop
        try:
            self.main_loop = asyncio.get_event_loop()
        except RuntimeError:
            self.main_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.main_loop)
            
        logger.info(f"[TELEGRAMBOT] 主循环已捕获: {self.main_loop}")

        # Run the bot until you press Ctrl-C
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

    def startup(self):
        self.main()

    def handler_single_msg(self, msg):
        try:
            cmsg = TelegramMessage(msg, False, self.main_loop)
        except NotImplementedError as e:
            logger.debug("[TELEGRAMBOT]single message {} skipped: {}".format(msg["MsgId"], e))
            error_reply = escape(e.args[0])
            self.application.bot.send_message(chat_id=msg.chat_id, text=error_reply)
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
        """
        兼容同步/异步/多线程的通用发送入口
        """
        # 1. 封装具体的发送任务（协程）
        coro = self._send_implementation(reply, context)
        
        # 2. 调度逻辑：自动判断当前环境
        try:
            # 尝试获取当前线程的 Loop
            loop = asyncio.get_running_loop()
            
            # 【情况A】主线程：直接创建任务
            loop.create_task(coro)
            logger.info("[TELEGRAMBOT] 检测到主线程环境，已使用 create_task 提交任务")
            
        except RuntimeError:
            # 【情况B】如果报错 "no running event loop"，说明我们在子线程里：跨线程提交给主 Loop
            logger.info("[TELEGRAMBOT] 检测到子线程环境，正在跨线程提交任务...")

            if self.main_loop and self.main_loop.is_running():
                asyncio.run_coroutine_threadsafe(coro, self.main_loop)
                logger.info("[TELEGRAMBOT] 已通过 run_coroutine_threadsafe 提交任务")
            else:
                # 最后的保底：如果实在拿不到 loop，说明程序状态不对
                logger.error("[TELEGRAMBOT] 无法获取主事件循环，消息发送失败！")

    async def _send_implementation(self, reply: Reply, context: Context):
        receiver = context["receiver"]
        try:
            if reply.type == ReplyType.TEXT:
                await self.application.bot.send_message(chat_id=receiver, text=reply.content)
                logger.info("[TELEGRAMBOT] sendMsg={}, receiver={}".format(reply.content, receiver))
            elif reply.type == ReplyType.ERROR:
                logger.error("[TELEGRAMBOT] sendMsg error, reply={}, receiver={}".format(reply, receiver))
                await self.application.bot.send_message(chat_id=receiver, text=const.ERROR_RESPONSE)
                logger.info("[TELEGRAMBOT] sendMsg={}, receiver={}".format(reply.content, receiver))
            elif reply.type == ReplyType.INFO:
                await self.application.bot.send_message(chat_id=receiver, text=reply.content)
                logger.info("[TELEGRAMBOT] sendMsg={}, receiver={}".format(reply, receiver))
            elif reply.type == ReplyType.VOICE:
                await self.application.bot.send_voice(chat_id=receiver, voice=reply.content)
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
                        await self.application.bot.send_message(chat_id=receiver, text=receiver)
                        logger.info("[TELEGRAMBOT] sendMsg={}, receiver={}".format(reply.content, receiver))
                    elif parts is not None:
                        reply_text = "\n".join(part.text for part in parts)
                        safe_reply_text = html.escape(reply_text)
                    if grouding_metadata is not None:
                        inline_url = self.get_search_sources(grouding_metadata)
                    reply_content = safe_reply_text + "\n\n" + inline_url
                    await self.application.bot.send_message(
                        chat_id=receiver, 
                        text=reply_content,
                        parse_mode='HTML',
                        disable_web_page_preview=True
                    )
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
                    await self.application.bot.send_photo( chat_id=receiver, photo=image_storage)
                    logger.info("[TELEGRAMBOT] sendImage url={}, receiver={}".format(img_url, receiver))
            elif reply.type == ReplyType.IMAGE:  # 从文件读取图片
                response = reply.content
                parts = response.candidates[0].content.parts
                if parts is None:
                    finish_reason = response.candidates[0].finish_reason
                    logger.error("[TELEGRAMBOT] sendMsg error, reply={}, receiver={}, error={}".format(reply, receiver, finish_reason))
                    await self.application.bot.send_message(chat_id=receiver, text=const.ERROR_RESPONSE)
                    logger.info("[TELEGRAMBOT] sendMsg={}, receiver={}".format(reply.content, receiver))
                else:
                    for part in parts:
                        if part.text:
                            reply_text = part.text
                            await self.application.bot.send_message(chat_id=receiver, text=reply_text)
                            logger.info("[TELEGRAMBOT_{}] sendMsg={}, receiver={}".format(conf().get('text_to_image'), reply_text, receiver))
                        elif part.inline_data:
                            image_bytes = part.inline_data.data
                            image = BytesIO(image_bytes)
                            logger.info(f"[TELEGRAMBOT_{conf().get('text_to_image')}] reply={image}")
                            image.seek(0)
                            await self.application.bot.send_photo(chat_id=receiver, photo=image)
                            logger.info("[TELEGRAMBOT_{}] sendMsg={}, receiver={}".format(conf().get('text_to_image'), image, receiver))
            elif reply.type == ReplyType.FILE:  # 新增文件回复类型
                file_pathes = reply.content['function_response']['file_pathes']
                reply_text = escape(reply.content['reply_text'])
                for file_path in file_pathes:
                    with open(file_path, "rb") as f:
                        await self.application.bot.send_document(chat_id=receiver, document=f)
                    logger.info("[TELEGRAMBOT] sendFile={}, receiver={}".format(file_path, receiver))
                await self.application.bot.send_message(chat_id=receiver, text=reply_text)
                logger.info("[TELEGRAMBOT] sendMsg={}, receiver={}".format(reply_text, receiver))
            elif reply.type == ReplyType.VIDEO:  # 新增视频回复类型
                video_storage = reply.content
                logger.debug(f"[TELEGRAMBOT] start download video, video_url={video_url}")
                video_res = requests.get(video_url, stream=True)
                video_storage = io.BytesIO()
                size = 0
                for block in video_res.iter_content(1024):
                    size += len(block)
                    video_storage.write(block)
                logger.info(f"[TELEGRAMBOT] download video success, size={size}, video_url={video_url}")
                video_storage.seek(0)
                await self.application.bot.send_video(chat_id=receiver, video=video_storage)
                logger.info("[TELEGRAMBOT] sendFile, receiver={}".format(receiver))
            elif reply.type == ReplyType.VIDEO_URL:  # 新增视频URL回复类型
                video_duration = reply.content[0]
                video_url = reply.content[1]          
                await self.application.bot.send_document(
                    chat_id=receiver, 
                    document=video_url, 
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=60
                )
                logger.info("[TELEGRAMBOT] sendVideo url={}, receiver={}".format(video_url, receiver))
        except Exception as e:
            logger.error("[TELEGRAMBOT] sendMsg error, reply={}, receiver={}, error={}".format(reply, receiver, e))
            # 发送失败时，尝试给用户回个错误提示
            await self.application.bot.send_message(chat_id=receiver, text=const.ERROR_RESPONSE)

    def get_search_sources(self, grounding_metadata):
        """
        Get search sources from the response Grounded with Google Search
        """
        sources = []
        ground_chunks = grounding_metadata.grounding_chunks
        for i, ground_chunk in enumerate(ground_chunks):
            title = ground_chunk.web.title
            uri = ground_chunk.web.uri

            #【HTML 转义】
            #title = escape(title)
            #safe_title = escape_markdown(title, version=2)
            #source = f'{i+1}\\. [{safe_title}]({uri})'

            # 【HTML 转义】非常重要！防止 title 里包含 < 或 > 导致 HTML 解析报错
            # 在 python 3 中可以使用 html.escape
            safe_title = html.escape(title)
            
            # 组装 HTML 格式: <a href="url">标题</a>
            source = f'{i+1}. <a href="{uri}">{safe_title}</a>'
            
            sources.append(source)
        inline_url = '\n'.join(sources)
        return inline_url