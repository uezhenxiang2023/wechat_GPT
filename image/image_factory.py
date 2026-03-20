from common import const

def create_image(image_type):
    """
    create a image_bot instance
    :param image_type: image_bot type code
    :return: image_bot instance
    """
    if image_type == const.KLING_V3_OMNI:
        from image.kling.kling_image import KlingImageBot
        return KlingImageBot()
    raise RuntimeError(f"Unsupported image model: {image_type}")