from common.expired_dict import ExpiredDict

USER_IMAGE_CACHE = ExpiredDict(60 *5)
USER_FILE_CACHE = ExpiredDict(60 * 10)
USER_VIDEO_CACHE = ExpiredDict(60 * 10)

USER_QUOTED_IMAGE_CACHE = ExpiredDict(60 * 5)
USER_QUOTED_VIDEO_CACHE = ExpiredDict(60 * 5)
