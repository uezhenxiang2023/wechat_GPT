# -*- coding=utf-8 -*-
import io
import time

import requests
from flask import Flask, request, abort
from wechatpy.enterprise import parse_message
from wechatpy.enterprise.crypto import WeChatCrypto
from wechatpy.enterprise.exceptions import InvalidCorpIdException
from wechatpy.exceptions import InvalidSignatureException, WeChatClientException

from bridge.context import Context
from bridge.reply import Reply, ReplyType
from channel.chat_channel import ChatChannel
from channel.wechatcom.wechatcomapp_client import WechatComAppClient
from channel.wechatcom.wechatcomapp_message import WechatComAppMessage
from common.log import logger
from common.singleton import singleton
from common.utils import compress_imgfile, fsize, split_string_by_utf8_length
from config import conf

MAX_UTF8_LEN = 2048

flask_app = Flask(__name__)


@singleton
class WechatComAppChannel(ChatChannel):
    NOT_SUPPORT_REPLYTYPE = []

    def __init__(self, session_id=None):
        super().__init__()
        self.corp_id = conf().get("wechatcom_corp_id")
        self.secret = conf().get("wechatcomapp_secret")
        self.agent_id = conf().get("wechatcomapp_agent_id")
        self.token = conf().get("wechatcomapp_token")
        self.aes_key = conf().get("wechatcomapp_aes_key")
        logger.info(
            "[wechatcom] Initializing WeCom app channel, corp_id: {}, agent_id: {}".format(self.corp_id, self.agent_id)
        )
        self.crypto = WeChatCrypto(self.token, self.aes_key, self.corp_id)
        self.client = WechatComAppClient(self.corp_id, self.secret)

    def startup(self):
        port = conf().get("wechatcomapp_port", 9898)
        logger.info("[wechatcom] ✅ WeCom app channel started successfully")
        logger.info("[wechatcom] 📡 Listening on http://0.0.0.0:{}/wxcomapp/".format(port))
        logger.info("[wechatcom] 🤖 Ready to receive messages")
        flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

    def stop(self):
        # Flask dev server 不支持优雅停止，生产环境建议使用 gunicorn
        logger.info("[wechatcom] HTTP server stopped")

    def send(self, reply: Reply, context: Context):
        receiver = context["receiver"]
        if reply.type in [ReplyType.TEXT, ReplyType.ERROR, ReplyType.INFO]:
            reply_text = reply.content
            texts = split_string_by_utf8_length(reply_text, MAX_UTF8_LEN)
            if len(texts) > 1:
                logger.info("[wechatcom] text too long, split into {} parts".format(len(texts)))
            for i, text in enumerate(texts):
                self.client.message.send_text(self.agent_id, receiver, text)
                if i != len(texts) - 1:
                    time.sleep(0.5)
            logger.info("[wechatcom] Do send text to {}: {}".format(receiver, reply_text))
        elif reply.type == ReplyType.IMAGE_URL:
            img_url = reply.content
            pic_res = requests.get(img_url, stream=True)
            image_storage = io.BytesIO()
            for block in pic_res.iter_content(1024):
                image_storage.write(block)
            sz = fsize(image_storage)
            if sz >= 10 * 1024 * 1024:
                logger.info("[wechatcom] image too large, ready to compress, sz={}".format(sz))
                image_storage = compress_imgfile(image_storage, 10 * 1024 * 1024 - 1)
                logger.info("[wechatcom] image compressed, sz={}".format(fsize(image_storage)))
            image_storage.seek(0)
            try:
                response = self.client.media.upload("image", image_storage)
                logger.debug("[wechatcom] upload image response: {}".format(response))
            except WeChatClientException as e:
                logger.error("[wechatcom] upload image failed: {}".format(e))
                return
            self.client.message.send_image(self.agent_id, receiver, response["media_id"])
            logger.info("[wechatcom] sendImage url={}, receiver={}".format(img_url, receiver))
        elif reply.type == ReplyType.IMAGE:
            image_storage = reply.content
            sz = fsize(image_storage)
            if sz >= 10 * 1024 * 1024:
                logger.info("[wechatcom] image too large, ready to compress, sz={}".format(sz))
                image_storage = compress_imgfile(image_storage, 10 * 1024 * 1024 - 1)
                logger.info("[wechatcom] image compressed, sz={}".format(fsize(image_storage)))
            image_storage.seek(0)
            try:
                response = self.client.media.upload("image", image_storage)
                logger.debug("[wechatcom] upload image response: {}".format(response))
            except WeChatClientException as e:
                logger.error("[wechatcom] upload image failed: {}".format(e))
                return
            self.client.message.send_image(self.agent_id, receiver, response["media_id"])
            logger.info("[wechatcom] sendImage, receiver={}".format(receiver))


@flask_app.route("/wxcomapp/", methods=["GET"])
def query_get():
    channel = WechatComAppChannel()
    logger.info("[wechatcom] receive params: {}".format(request.args))
    try:
        signature = request.args.get("msg_signature")
        timestamp = request.args.get("timestamp")
        nonce = request.args.get("nonce")
        echostr = request.args.get("echostr")
        echostr = channel.crypto.check_signature(signature, timestamp, nonce, echostr)
    except InvalidSignatureException:
        abort(403)
    return echostr


@flask_app.route("/wxcomapp/", methods=["POST"])
def query_post():
    channel = WechatComAppChannel()
    logger.info("[wechatcom] receive params: {}".format(request.args))
    try:
        signature = request.args.get("msg_signature")
        timestamp = request.args.get("timestamp")
        nonce = request.args.get("nonce")
        message = channel.crypto.decrypt_message(request.data, signature, timestamp, nonce)
    except (InvalidSignatureException, InvalidCorpIdException):
        abort(403)
    msg = parse_message(message)
    logger.debug("[wechatcom] receive message: {}, msg= {}".format(message, msg))
    if msg.type == "event":
        if msg.event == "subscribe":
            pass
            # reply_content = subscribe_msg()
            # if reply_content:
            #     reply = create_reply(reply_content, msg).render()
            #     res = channel.crypto.encrypt_message(reply, nonce, timestamp)
            #     return res
    else:
        try:
            wechatcom_msg = WechatComAppMessage(msg, client=channel.client)
        except NotImplementedError as e:
            logger.debug("[wechatcom] " + str(e))
            return "success"
        context = channel._compose_context(
            wechatcom_msg.ctype,
            wechatcom_msg.content,
            isgroup=False,
            msg=wechatcom_msg,
        )
        if context:
            channel.produce(context)
    return "success"