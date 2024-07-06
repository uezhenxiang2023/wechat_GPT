"""
google voice service
"""
import json

import openai
from openai import OpenAI

from bridge.reply import Reply, ReplyType
from common.log import logger
from config import conf
from voice.voice import Voice
import requests
from common import const
import datetime, random

client = OpenAI(api_key=conf().get("open_ai_api_key"))
ASR_MODEL = conf().get("voice_to_text_model")
TTS_MODEL = conf().get("text_to_voice_model")

class OpenaiVoice(Voice):

    def voiceToText(self, voice_file):
        logger.debug("[Openai] voice file name={}".format(voice_file))
        try:
            file = open(voice_file, "rb")
            result = client.audio.transcriptions.create(model=ASR_MODEL, file=file)
            text = result.text
            reply = Reply(ReplyType.TEXT, text)
            logger.info("OpenAI[{}] voiceToText text={} voice file name={}".format(ASR_MODEL, text, voice_file))
        except Exception as e:
            reply = Reply(ReplyType.ERROR, "我暂时还无法听清您的语音，请稍后再试吧~")
        finally:
            return reply


    def textToVoice(self, text):
        try:
            response = client.audio.speech.create(
                model=TTS_MODEL,
                voice=conf().get("tts_voice_id"),
                input=text
            )
            file_name = "tmp/" + datetime.datetime.now().strftime('%Y%m%d%H%M%S') + str(random.randint(0, 1000)) + ".mp3"
            logger.debug(f"[OpenAI] text_to_Voice file_name={file_name}, input={text}")
            response.write_to_file(file_name)
            logger.info(f"OpenAI[{TTS_MODEL}] text_to_Voice success")
            reply = Reply(ReplyType.VOICE, file_name)
        except Exception as e:
            logger.error(e)
            reply = Reply(ReplyType.ERROR, "遇到了一点小问题，请稍后再问我吧")
        return reply
