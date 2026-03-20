from common import const

def create_video(video_type):
    """
    create a video_bot instance
    :param video_type: video_bot type code
    :return: video_bot instance
    """
    if video_type == const.KLING_V3_OMNI:
        from video.kling.kling_video import KlingVideoBot
        return KlingVideoBot()
    raise RuntimeError(f"Unsupported image model: {video_type}")