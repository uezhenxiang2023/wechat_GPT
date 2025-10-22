"""
Bytedance volcengine_ark bot

@author fort
@Date 2025/10/19
"""

import os
import base64
import time

from volcenginesdkarkruntime import Ark

from config import conf
from bot.bot import Bot
from bot.session_manager import SessionManager
from bot.chatgpt.chat_gpt_session import ChatGPTSession
from bridge.context import ContextType, Context
from bridge.reply import Reply, ReplyType
from common import memory
from common.log import logger
from common.tool_button import tool_state

class VolcengineArkBot(Bot):

    def __init__(self):
        super().__init__()
        self.api_key = conf().get("ark_api_key")
        self.model = conf().get('model')
        self.Model_ID = self.model.upper()
        self.thinking = conf().get('thinking')
        self.image_model = conf().get('text_to_image')
        self.IMAGE_MODEL_ID = self.image_model.upper()
        self.video_model = conf().get('text_to_video')
        self.VIDEO_MODEL_ID = self.video_model.upper()
        self.image_size = conf().get('image_create_size')
        self.system_prompt = conf().get("character_desc") 
        self.sessions = SessionManager(ChatGPTSession, model=self.model or "gpt-3.5-turbo") # 复用chatGPT的token计算方式

        self.client = Ark(
            api_key=self.api_key,
            # The base URL for model invocation .
            base_url="https://ark.cn-beijing.volces.com/api/v3",
        )

    def reply(self, query, context: Context = None) -> Reply:
        try:
            if context.type == ContextType.TEXT:
                session_id = context["session_id"]
                # 使用用户特定的状态筛选模型
                is_imaging = tool_state.get_image_state(session_id)
                is_editing = tool_state.get_edit_state(session_id)
                if is_imaging:
                    Model_ID = self.IMAGE_MODEL_ID
                elif is_editing:
                    Model_ID = self.VIDEO_MODEL_ID
                else:
                    Model_ID = self.Model_ID
                logger.info(f"[{Model_ID}] query={query}, requester={session_id}")

                # 检查缓存中是否媒体文件
                file_cache = memory.USER_IMAGE_CACHE.get(session_id)
                if not is_editing:
                    # 关闭视频编辑功能
                    if not is_imaging:
                        # 关闭图片编辑功能
                        if file_cache:
                            # 图片理解应用
                            text_content = {
                                'type': 'text',
                                'text': query
                            }
                            image_contents = []
                            image_files = file_cache['files']
                            image_pathes = file_cache['path']
                            memory.USER_IMAGE_CACHE.pop(session_id)
                            for i, v in enumerate(image_pathes):
                                for m, n in enumerate(image_files):
                                    if m == i:
                                        image_content = self.encode_image_content(v, n)
                                        image_contents.append(image_content)
                                        break
                            image_contents.append(text_content)
                            query = image_contents
                        session = self.sessions.session_query(query, session_id)
                        messages = session.messages
                        completion = self.client.chat.completions.create(
                            # Get Model ID: https://www.volcengine.com/docs/82379/1330310 .
                            model=self.model,
                            messages=messages,
                            thinking = {
                                "type": self.thinking
                            }
                        )
                        reply_text = completion.choices[0].message.content
                        total_tokens = completion.usage.total_tokens
                        self.sessions.session_reply(reply_text, session_id, total_tokens) 
                        return Reply(ReplyType.TEXT, reply_text)
                    elif is_imaging:
                        # 开启图片编辑功能
                        if file_cache:
                            images = []
                            image_files = file_cache['files']
                            image_pathes = file_cache['path']
                            memory.USER_IMAGE_CACHE.pop(session_id)
                            for i, v in enumerate(image_pathes):
                                for m, n in enumerate(image_files):
                                    if m == i:
                                        image = self.encode_image(v, n)
                                        images.append(image)
                                        break
                            image_size = self.size_calculator(image_files)
                            images_response = self.client.images.generate(
                                model = self.image_model,
                                prompt=query,
                                image=images,
                                response_format='url',
                                size=image_size,
                                watermark=True,
                                sequential_image_generation='disabled'
                            )
                        else:
                            images_response = self.client.images.generate(
                                model = self.image_model,
                                prompt=query,
                                response_format='url',
                                size=self.image_size,
                                watermark=True,
                                sequential_image_generation='disabled'
                            )
                        image_url = images_response.data[0].url
                        return Reply(ReplyType.IMAGE_URL, image_url)
                elif is_editing:
                    # 开启视频编辑功能
                    if file_cache:
                        text_content = [
                            {
                                'type': 'text',
                                'text': query + "--resolution 1080p  --duration 5 --camerafixed false --watermark true"
                            }
                        ]
                        images = []
                        image_files = file_cache['files']
                        image_pathes = file_cache['path']
                        for i, v in enumerate(image_pathes):
                            for m, n in enumerate(image_files):
                                if m == i:
                                    image = self.encode_image_content(v, n)
                                    images.append(image)
                                    break
                        text_content.extend(images)
                        video_response = self.client.content_generation.tasks.create(
                            model = self.video_model,
                            content=text_content
                        )
                    else:
                        video_response = self.client.content_generation.tasks.create(
                            model = self.video_model,
                            content=[
                                {
                                    'type': 'text',
                                    'text': query + "--resolution 480p  --duration 5 --camerafixed false --watermark true"
                                }
                            ]
                        )
                    task_id = video_response.id
                    video_info = self.get_video_info(task_id)
                    return Reply(ReplyType.VIDEO_URL, video_info)             

        except Exception as e:
            logger.error("[{}] fetch reply error, {}".format(self.Model_ID, e))
            return Reply(ReplyType.ERROR, f"[{self.Model_ID}] {e}")
    
    def encode_image_content(self, image_path, image_file):
        "“将图片信息组装为Base64消息体，作为用户消息发给多模态模型，用于图片理解类任务”"
        with open(image_path, 'rb') as file:
            base64_image = base64.b64encode(file.read()).decode('utf-8')
        image_type = type(image_file).__name__
        if image_type == 'JpegImageFile':
            image_content = {
                'type': 'image_url',
                'image_url': {
                    'url': f"data:image/jpeg;base64,{base64_image}"
                }
            }
        elif image_type == 'PngImageFile':
            image_content = {
                'type': 'image_url',
                'image_url': {
                    'url': f"data:image/png;base64,{base64_image}"
                }
            }
        return image_content
    
    def encode_image(self, image_path, image_file):
        "“将图片转为Base64编码的恶字符串，发给图片生成模型，用于图片生成类任务”"
        with open(image_path, 'rb') as file:
            base64_image = base64.b64encode(file.read()).decode('utf-8')
        image_type = type(image_file).__name__
        if image_type == 'JpegImageFile':
            image = f"data:image/jpeg;base64,{base64_image}"
        elif image_type == 'PngImageFile':
            image = f"data:image/png;base64,{base64_image}"
        return image
    
    def get_video_info(self, id):
        print(f"----- [{self.VIDEO_MODEL_ID}]polling task status -----")
        while True:
            get_result = self.client.content_generation.tasks.get(task_id=id)
            status = get_result.status
            if status == "succeeded":
                print(f"----- [{self.VIDEO_MODEL_ID}]task succeeded -----")
                video_duration = get_result.duration
                video_url = get_result.content.video_url
                break
            elif status == "failed":
                print(f"----- [{self.VIDEO_MODEL_ID}]task failed -----")
                print(f"Error: {get_result.error}")
                break
            else:
                print(f"Current status: {status}, Retrying after 3 seconds...")
                time.sleep(3)
        return (video_duration, video_url)
    
    def size_calculator(self, files):
        """推断生成图片的最佳分辨率"""
        # 预定义的宽高比和对应的分辨率
        ratio_resolutions = {
            1.0: '2048x2048',    # 1:1
            1.33: '2304x1728',   # 4:3
            0.75: '1728x2304',   # 3:4
            1.78: '2560x1440',   # 16:9
            0.56: '1440x2560',   # 9:16
            1.5: '2496x1664',    # 3:2
            0.67: '1664x2496',   # 2:3
            2.33: '3024x1296'    # 21:9
        }
        
        # 获取所有图片中最大尺寸的宽高比
        sizes_list = []
        for file in files:
            size = file.size
            sizes_list.append(size)
        sorted_list = sorted(sizes_list, key=lambda x: x[0] * x[1], reverse=True)
        best_size = sorted_list[0]
        ratio = round(best_size[0] / best_size[1], 2)
        
        # 找到最接近的预定义宽高比
        closest_ratio = min(ratio_resolutions.keys(), key=lambda x: abs(x - ratio))
        
        return ratio_resolutions[closest_ratio]