from common import const

def create_video(video_type):
    """
    create a video_bot instance
    :param video_type: video_bot type code
    :return: video_bot instance
    """
    if video_type in const.KLING_VIDEO_LIST:
        from video.kling.kling_video import KlingVideoBot
        return KlingVideoBot()
    if video_type in const.DOUBAO_SEEDANCE_LIST:
        from video.doubao.doubao_video import DoubaoVideoBot
        return DoubaoVideoBot()
    raise RuntimeError(f"Unsupported image model: {video_type}")
