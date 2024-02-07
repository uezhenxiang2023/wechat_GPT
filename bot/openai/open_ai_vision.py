import base64


from common.log import logger
from common import const, utils, memory
from config import conf
from openai import OpenAI

client = OpenAI(api_key=conf().get("open_ai_api_key")) # Instantiage a client accordinng to the latest openai SDK

# OPENAI提供的图像识别接口
class OpenAIVision(object):
    def do_vision_completion_if_need(self, session_id: str, query: str):
        img_cache = memory.USER_IMAGE_CACHE.get(session_id)
        if img_cache and conf().get("image_recognition"):
            response, err = self.vision_completion(query, img_cache)
            if err:
                return {"completion_tokens": 0, "content": f"识别图片异常, {err}"}
            memory.USER_IMAGE_CACHE[session_id] = None
            return {
                "total_tokens": response.usage.total_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "content": response.choices[0].message.content,
            }
        return None

    def vision_completion(self, query: str, img_cache: dict):
        msg = img_cache.get("msg")
        path = img_cache.get("path")
        msg.prepare()
        logger.info(f"[GPT-4-VISION-PREVIEW] query with images, path={path}")
        
        # Request the gpt-4-vision-preview with the latest openai SDK
        try:
            res = client.chat.completions.create(
                model=const.GPT4_VISION_PREVIEW,
                messages=self.build_vision_msg(query,path),
                max_tokens=300
            )
            
            return res,None
        except Exception as e:
            logger.error(f"[GPT-4-VISION-PREVIEW] vision completion, err response={e}")
            return None, e

    def build_vision_msg(self, query: str, path: str):
        suffix = utils.get_path_suffix(path)
        with open(path, "rb") as file:
            base64_str = base64.b64encode(file.read()).decode('utf-8')
        messages = [{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": query
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/{suffix};base64,{base64_str}"
                    }
                }
            ]
        }]
        return messages
