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
from common import const, memory
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
            api_key=self.api_key
        )

    def reply(self, query, context: Context = None) -> Reply:
        try:
            if context.type != ContextType.TEXT:
                return Reply(ReplyType.ERROR, "Only text context is supported")

            session_id = context["session_id"]
            is_imaging = tool_state.get_image_state(session_id)
            is_editing = tool_state.get_edit_state(session_id)

            # 确定使用的模型
            model_id = (self.IMAGE_MODEL_ID if is_imaging else 
                   self.VIDEO_MODEL_ID if is_editing else 
                   self.Model_ID)
            logger.info(f"[{model_id}] query={query}, requester={session_id}")

            # 检查缓存中是否媒体文件
            file_cache = memory.USER_IMAGE_CACHE.get(session_id)

            # 文本对话模式
            if not is_imaging and not is_editing:
                if file_cache:
                    image_contents = self._process_image_files(file_cache)
                    image_contents.append({'type': 'text', 'text': query})
                    query = image_contents
                    memory.USER_IMAGE_CACHE.pop(session_id)
                    
                session = self.sessions.session_query(query, session_id)
                client_attr = 'bot_chat' if self.model in const.DOUBAO_BOT else 'chat'
                # 如果调用bot,去掉messages列表中的system prompt
                if client_attr == 'bot_chat' and session.messages[0].get('role') == 'system':
                    session.messages.pop(0)
                completion = getattr(self.client, client_attr).completions.create(
                    model=self.model,
                    messages=session.messages,
                    #thinking={"type": self.thinking}
                )
                reply_text = completion.choices[0].message.content
                total_tokens = completion.usage.total_tokens if client_attr == 'chat' else completion.bot_usage.model_usage[0].total_tokens
                self.sessions.session_reply(reply_text, session_id, total_tokens)
                return Reply(ReplyType.TEXT, reply_text)
            
            # 图片生成模式
            elif is_imaging:
                params = {
                    'model': self.image_model,
                    'prompt': query,
                    'response_format': 'url',
                    'watermark': True,
                    'sequential_image_generation': 'disabled'
                }
                
                if file_cache:
                    images = [self.encode_image(path, file) 
                            for path, file in zip(file_cache['path'], file_cache['files'])]
                    params.update({
                        'image': images,
                        'size': self.size_calculator(file_cache['files'])
                    })
                    memory.USER_IMAGE_CACHE.pop(session_id)
                else:
                    params['size'] = self.image_size
                    
                response = self.client.images.generate(**params)
                return Reply(ReplyType.IMAGE_URL, response.data[0].url)
            
            # 视频生成模式
            else:
                content = [{
                    'type': 'text',
                    'text': f"{query} --resolution {'1080p' if file_cache else '480p'} --duration 5 --camerafixed false --watermark true"
                }]
                
                if file_cache:
                    image_contents = self._process_image_files(file_cache)
                    content.extend(image_contents)
                    memory.USER_IMAGE_CACHE.pop(session_id)
                    
                response = self.client.content_generation.tasks.create(
                    model=self.video_model,
                    content=content
                )
                return Reply(ReplyType.VIDEO_URL, self.get_video_info(response.id))             

        except Exception as e:
            logger.error(f"[{model_id}] fetch reply error, {e}")
            return Reply(ReplyType.ERROR, f"[{model_id}] {e}")
    
    def _process_image_files(self, file_cache):
        """处理图片文件缓存,返回处理后的图片内容列表"""
        if not file_cache:
            return []
        image_contents = []
        image_files = file_cache['files']
        image_pathes = file_cache['path']
        for i, path in enumerate(image_pathes):
            for j, file in enumerate(image_files):
                if i == j:
                    image_content = self.encode_image_content(path, file)
                    image_contents.append(image_content)
                    break
        return image_contents
    
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