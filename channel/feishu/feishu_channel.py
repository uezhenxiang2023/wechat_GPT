"""
飞书通道接入

@author uezhenxiang2023
@Date 2025/05/11
"""

# -*- coding=utf-8 -*-
import io, json, os, uuid, requests
from io import BytesIO
from flask import Flask

from channel.feishu.feishu_message import FeishuMessage
from bridge.context import Context
from bridge.reply import Reply, ReplyType
from common.log import logger
from common.singleton import singleton
from common import const, tool_button
from common.tmp_dir import TmpDir
from config import conf
from bridge.context import ContextType
from channel.chat_channel import ChatChannel
from channel.chat_message import ChatMessage
import lark_oapi as lark
from lark_oapi.api.im.v1 import *
from lark_oapi.adapter.flask import *
from channel.telegram.telegram_text_util import escape
from common.tmp_dir import TmpDir, create_user_dir


@singleton
class FeiShuChanel(ChatChannel):
    def __init__(self):
        super().__init__()
        self.app_id = conf().get('feishu_app_id')
        self.app_secret = conf().get('feishu_app_secret')
        self.encrypt_key = conf().get('feishu_encrypt_key')
        self.verification_token = conf().get('feishu_verify_token')
        self.websocket = conf().get('feishu_websocket')
        self.CLIENT_ENCRYPT_KEY = "" if self.websocket is True else self.encrypt_key
        self.CLIENT_VERIFICATION_TOKEN = "" if self.websocket is True else self.verification_token
        # 初始化 Flask app
        self.app = Flask(__name__)
        # 注册路由
        self.app.route("/", methods=["POST"])(self.handle_webhook_event)
        self.webhook_port = conf().get('feishu_webhook_port')
        # Register event handler.
        self.event_handler = (
            lark.EventDispatcherHandler.builder(self.CLIENT_ENCRYPT_KEY, self.CLIENT_VERIFICATION_TOKEN)
            .register_p2_im_message_receive_v1(self.do_p2_im_message_receive_v1)
            .register_p2_application_bot_menu_v6(self.do_p2_application_bot_menu_v6)
            .build()
        )
        # Create LarkClient object for requesting OpenAPI, and create LarkWSClient object for receiving events using long connection.
        self.client = (
            lark.Client.builder()
            .app_id(self.app_id)
            .app_secret(self.app_secret)
            .build()
            )
        self.wsClient = lark.ws.Client(
            self.app_id,
            self.app_secret,
            event_handler=self.event_handler,
            log_level=lark.LogLevel.INFO,
        )

    # Register event handler to handle received messages.
    # https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/events/receive
    def do_p2_im_message_receive_v1(self, data: P2ImMessageReceiveV1) -> None:
        if data.event.message.chat_type == "p2p":
            self.handler_single_msg(data.event)

        elif data.event.message.chat_type == "group":
            self.handler_group_msg(data.event)
            """request: ReplyMessageRequest = (
                ReplyMessageRequest.builder()
                .message_id(data.event.message.message_id)
                .request_body(
                    ReplyMessageRequestBody.builder()
                    .content(content)
                    .msg_type("text")
                    .build()
                )
                .build()
            )
            # Reply to messages using reply OpenAPI
            # https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/reply
            response: ReplyMessageResponse = self.client.im.v1.message.reply(request)
            if not response.success():
                raise Exception(
                    f"client.im.v1.message.reply failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}"
                )"""
    # Register event handler to handle bot menu.
    # https://open.feishu.cn/document/client-docs/bot-v3/events/menu
    def do_p2_application_bot_menu_v6(self, data: lark.application.v6.P2ApplicationBotMenuV6) -> None:
        print(f'[ do_p2_application_bot_menu_v6 access ], data: {lark.JSON.marshal(data, indent=4)}')
        event_key = data.event.event_key
        open_id = data.event.operator.operator_id.open_id
        if event_key == 'imaging':
            self.image(open_id)
        elif event_key == 'searching':
            self.search(open_id)

    def handle_webhook_event(self):
        """Webhook event handler"""
        try:
            # 获取请求数据
            event_data = parse_req()
            resp = self.event_handler.do(event_data)
            return parse_resp(resp)
        except Exception as e:
            logger.error(f"[Lark]Error handling webhook event: {str(e)}")
            return {"[Lark]error": str(e)}, 500
    
    def search(self, toUserName) -> None:
        """
        This function handles the search menu
        """
        if tool_button.searching:
            text = "联网功能已关闭，如果需要，可以通过消息输入框左侧的命令菜单随时开启。"
        elif not tool_button.searching:
            text = "联网搜索功能已开启，需要我帮你查询点啥？"
        tool_button.searching = not tool_button.searching
        self.send_text(text, toUserName)
        
    
    def image(self, toUserName) -> None:
        """
        This function handles image menu
        """
        if tool_button.imaging:
            text = "图片生成功能已关闭，如果需要，可以通过消息输入框左侧的命令菜单随时开启。"
        elif not tool_button.imaging:
            text = "图片生成功能已开启，需要我帮你弄点啥图？"
        tool_button.imaging = not tool_button.imaging
        self.send_text(text, toUserName)

    def main(self):
        if self.websocket is True:
            # 使用websocket长链接接收飞书事件.
            self.wsClient.start()
        else:
            # 使用webhook模式，通过本地服务器接收飞书事件
            logger.info(f"Starting webhook server on port {self.webhook_port}")
            self.app.run(
                host='0.0.0.0',  # 允许外部访问
                port=self.webhook_port,
                debug=False  # 生产环境建议设为False
            )
    def startup(self):
        self.main()

    def handler_single_msg(self, msg):
        try:
            cmsg = FeishuMessage(msg, False)
        except NotImplementedError as e:
            logger.debug("[Lark]single message {} skipped: {}".format(msg["MsgId"], e))
            error_reply = e
            self.send_text(error_reply, msg.chat_id)
            return None
        self.handle_single(cmsg)
        return None

    def handler_group_msg(self, msg):
        try:
            cmsg = FeishuMessage(msg, True)
        except NotImplementedError as e:
            logger.debug("[Lark]group message {} skipped: {}".format(msg["MsgId"], e))
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
            logger.debug("[Lark]receive voice msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.IMAGE:
            logger.debug("[Lark]receive image msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.VIDEO:
            logger.debug("[Lark]receive video msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.SHARING:
            logger.debug("[Lark]receive url msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.PATPAT:
            logger.debug("[Lark]receive patpat msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.FILE:
            logger.debug("[Lark]receive file msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.TEXT:
            logger.debug("[Lark]receive text msg: {}, cmsg={}".format(cmsg.content, cmsg))
        else:
            logger.debug("[Lark]receive msg: {}, cmsg={}".format(cmsg.content, cmsg))
        context = self._compose_context(cmsg.ctype, cmsg.content, isgroup=False, msg=cmsg)
        if context:
            self.produce(context)

    def handle_group(self, cmsg: ChatMessage):
        if cmsg.ctype == ContextType.VOICE:
            if conf().get("group_speech_recognition") != True:
                return
            logger.debug("[Lark]receive voice for group msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.IMAGE:
            logger.debug("[Lark]receive image for group msg: {}".format(cmsg.content))
        elif cmsg.ctype in [ContextType.JOIN_GROUP, ContextType.PATPAT, ContextType.ACCEPT_FRIEND, ContextType.EXIT_GROUP]:
            logger.debug("[Lark]receive note msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.TEXT:
            logger.debug("[Lark]receive group msg: {}, cmsg={}".format(cmsg.content, cmsg))
            pass
        elif cmsg.ctype == ContextType.FILE:
            logger.debug(f"[Lark]receive attachment msg, file_name={cmsg.content}")
        else:
            logger.debug("[Lark]receive group msg: {}".format(cmsg.content))
        context = self._compose_context(cmsg.ctype, cmsg.content, isgroup=True, msg=cmsg)
        if context:
            self.produce(context)

    # 统一的发送函数，每个Channel自行实现，根据reply的type字段发送不同类型的消息
    def send(self, reply: Reply, context: Context):
        receiver = context["receiver"]
        error_response = "网络有点小烦忙，请过几秒再试一试，给您带来不便，大超子深表歉意"
        if reply.type == ReplyType.TEXT:
            try:
                self.send_text(reply.content, toUserName=receiver)
                logger.info("[Lark] sendMsg={}, receiver={}".format(reply, receiver))
            except Exception as e:
                logger.error("[Lark] sendMsg error, reply={}, receiver={}, error={}".format(reply, receiver, e))
                self.send_text(error_response, toUserName=receiver)
                logger.info("[Lark] sendMsg={}, receiver={}".format(error_response, receiver))
        elif reply.type == ReplyType.ERROR:
            self.send_text(error_response, toUserName=receiver)
            logger.info("[Lark] sendMsg={}, receiver={}".format(error_response, receiver))
        elif reply.type == ReplyType.INFO:
            self.send_text(escape(reply.content), toUserName=receiver)
            logger.info("[Lark] sendMsg={}, receiver={}".format(reply, receiver))
        elif reply.type == ReplyType.VOICE:
            self.send_file(reply.content, toUserName=receiver)
            logger.info("[Lark] sendFile={}, receiver={}".format(reply.content, receiver))
        elif reply.type == ReplyType.IMAGE_URL:  # 从网络下载图片
            img_url = reply.content
            logger.debug(f"[Lark] start download image, img_url={img_url}")
            pic_res = requests.get(img_url, stream=True)
            image_storage = io.BytesIO()
            size = 0
            for block in pic_res.iter_content(1024):
                size += len(block)
                image_storage.write(block)
            logger.info(f"[Lark] download image success, size={size}, img_url={img_url}")
            image_storage.seek(0)
            self.send_image(image_storage, toUserName=receiver)
            logger.info("[Lark] sendImage url={}, receiver={}".format(img_url, receiver))
        elif reply.type == ReplyType.IMAGE:  # 从文件读取图片
            response = reply.content
            parts = response.candidates[0].content.parts
            if parts is None:
                finish_reason = response.candidates[0].finish_reason
                logger.error("[Lark] sendMsg error, reply={}, receiver={}, error={}".format(reply, receiver, finish_reason))
                self.send_text(const.ERROR_RESPONSE, toUserName=receiver)
                logger.info("[Lark] sendMsg={}, receiver={}".format(reply.content, receiver))
            else:
                for part in parts:
                    if part.text:
                        reply_text = part.text
                        self.send_text(reply_text, receiver)
                        logger.info("[Lark_GEMINI-2.0-FLASH-EXP] sendMsg={}, receiver={}".format(part.text, receiver))
                    elif part.inline_data:
                        image_type = part.inline_data.mime_type.split('/')[-1]
                        image = BytesIO(part.inline_data.data)
                        logger.info(f"[Lark_GEMINI-2.0-FLASH-EXP] reply={image}")
                        image.seek(0)
                        user_dir = TmpDir().path() + str(receiver) + '/response/'
                        user_dir_exists = os.path.exists(user_dir)
                        if not user_dir_exists:
                            create_user_dir(user_dir)
                        response_uuid = str(uuid.uuid4())
                        image_path = user_dir + response_uuid + '.' + image_type
                        with open(image_path, 'wb') as f:
                            f.write(image.read())
                        self.send_image(image_path, receiver)
                        logger.info("[Lark_GEMINI-2.0-FLASH-EXP] sendMsg={}, receiver={}".format(image, receiver))
        elif reply.type == ReplyType.FILE:  # 新增文件回复类型
            file_pathes = reply.content['function_response']['file_pathes']
            reply_text = reply.content['reply_text']
            for file_path in file_pathes:
                self.send_file(file_path, toUserName=receiver)
                logger.info("[Lark] sendFile={}, receiver={}".format(file_path, receiver))
            self.send_text(reply_text, receiver)
            logger.info("[Lark] sendMsg={}, receiver={}".format(reply_text, receiver))
        elif reply.type == ReplyType.VIDEO:  # 新增视频回复类型
            video_storage = reply.content
            self.send_video(video_storage, toUserName=receiver)
            logger.info("[Lark] sendFile, receiver={}".format(receiver))
        elif reply.type == ReplyType.VIDEO_URL:  # 新增视频URL回复类型
            video_url = reply.content
            logger.debug(f"[Lark] start download video, video_url={video_url}")
            video_res = requests.get(video_url, stream=True)
            video_storage = io.BytesIO()
            size = 0
            for block in video_res.iter_content(1024):
                size += len(block)
                video_storage.write(block)
            logger.info(f"[Lark] download video success, size={size}, video_url={video_url}")
            video_storage.seek(0)
            self.send_video(video_storage, toUserName=receiver)
            logger.info("[Lark] sendVideo url={}, receiver={}".format(video_url, receiver))

    def send_text(self, reply_content, toUserName):
        """
        This function sends a response text message back to the user.
        """
        content = json.dumps(
            {
                "text": reply_content
            }
        )
        request = (
                CreateMessageRequest.builder()
                .receive_id_type("open_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(toUserName)
                    .msg_type("text")
                    .content(content)
                    .build()
                )
                .build()
            )
        # Use send OpenAPI to send messages
        # https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/create
        response = self.client.im.v1.chat.create(request)

        if not response.success():
            raise Exception(
                f"client.im.v1.chat.create failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}"
            )

    def send_image(self, reply_content, toUserName):
        """
        This function sends a response image back to the user.
        """
        # 创建client
        client = self.client
        image_key = self.create_image(reply_content)
        content = json.dumps(
            {
                "image_key": image_key
            }
        )

        # 生成唯一的UUID
        request_uuid = str(uuid.uuid4())

        # 构造请求对象
        request: CreateMessageRequest = CreateMessageRequest.builder() \
            .receive_id_type("open_id") \
            .request_body(CreateMessageRequestBody.builder()
                .receive_id(toUserName)
                .msg_type("image")
                .content(content)
                .uuid(request_uuid)
                .build()) \
            .build()

        # 发起请求
        response: CreateMessageResponse = client.im.v1.message.create(request)

        # 处理失败返回
        if not response.success():
            lark.logger.error(
                f"client.im.v1.message.create failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}, resp: \n{json.dumps(json.loads(response.raw.content), indent=4, ensure_ascii=False)}")
            return
        # 处理业务结果
        lark.logger.info(lark.JSON.marshal(response.data, indent=4))
    
    def create_image(self, image_path):
        """
        This function uploads image to Lark OpenAPI.
        """
        # 创建client
        client = self.client

        # 构造请求对象
        file = open(image_path, "rb")
        request: CreateImageRequest = CreateImageRequest.builder() \
            .request_body(CreateImageRequestBody.builder()
                .image_type("message")
                .image(file)
                .build()) \
            .build()

        # 发起请求
        response: CreateImageResponse = client.im.v1.image.create(request)

        # 处理失败返回
        if not response.success():
            lark.logger.error(
                f"client.im.v1.image.create failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}, resp: \n{json.dumps(json.loads(response.raw.content), indent=4, ensure_ascii=False)}")
            return
        # 处理业务结果
        else:
            lark.logger.info(lark.JSON.marshal(response.data, indent=4))
            return response.data.image_key

    def create_file(self, file_path):
        """
        This function uploads file to Lark OpenAPI.
        """
        # 创建client
        client = self.client
        # 构造请求对象
        filename = os.path.basename(file_path)
        name, ext = os.path.splitext(filename)
        ext = ext.lstrip('.')
        file = open(file_path, "rb")
        request: CreateFileRequest = CreateFileRequest.builder() \
            .request_body(CreateFileRequestBody.builder()
                .file_type(ext)
                .file_name(filename)
                .file(file)
                .build()) \
            .build()

        # 发起请求
        response: CreateFileResponse = client.im.v1.file.create(request)

        # 处理失败返回
        if not response.success():
            lark.logger.error(
                f"client.im.v1.file.create failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}, resp: \n{json.dumps(json.loads(response.raw.content), indent=4, ensure_ascii=False)}")
            return

        else:
            # 处理业务结果
            lark.logger.info(lark.JSON.marshal(response.data, indent=4))
            return response.data.file_key

    def send_file(self, reply_content, toUserName):
        """
        This function sends a response file back to the user.
        """
        # 创建client
        client = self.client
        file_key = self.create_file(reply_content)
        content = json.dumps(
            {
                "file_key": file_key
            }
        )

        # 生成唯一的UUID
        request_uuid = str(uuid.uuid4())

        # 构造请求对象
        request: CreateMessageRequest = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(CreateMessageRequestBody.builder()
                .receive_id(toUserName)
                .msg_type("file")
                .content(content)
                .uuid(request_uuid)
                .build()) \
            .build()

        # 发起请求
        response: CreateMessageResponse = client.im.v1.message.create(request)

        # 处理失败返回
        if not response.success():
            lark.logger.error(
                f"client.im.v1.message.create failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}, resp: \n{json.dumps(json.loads(response.raw.content), indent=4, ensure_ascii=False)}")
            return

        # 处理业务结果
        lark.logger.info(lark.JSON.marshal(response.data, indent=4))
