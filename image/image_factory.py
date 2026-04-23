from common import const

def create_image(image_type):
    """
    create a image_bot instance
    :param image_type: image_bot type code
    :return: image_bot instance
    """
    if image_type in (const.KLING_IMAGE_LIST + const.KLING_OMNI_IMAGE_LIST):
        from image.kling.kling_image import KlingImageBot
        return KlingImageBot()
    if image_type in const.DOUBAO_SEEDREAM_LIST:
        from image.doubao.doubao_image import DoubaoImageBot
        return DoubaoImageBot()
    if image_type in const.GOOGLE_IMAGE_LIST:
        from image.google.google_gemini_image import GoogleGeminiImageBot
        return GoogleGeminiImageBot()
    if image_type in const.GROK_IMAGE_LIST:
        from image.grok.grok_image import GrokImageBot
        return GrokImageBot()
    if image_type in const.GPT_IMAGE_LIST:
        from image.openai.gpt_image import GPTImageBot
        return GPTImageBot()
    raise RuntimeError(f"Unsupported image model: {image_type}")
