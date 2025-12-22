"""聊天窗口中的视频状态"""

from config import conf

class UserVideoState:
    def __init__(self):
        self._video_states = {}
    
    def __get_video_state__(self, user_id):
        """获取用户模型状态,如果不存在则创建新的"""
        if user_id not in self._video_states:
            self._video_states[user_id] = {
                'duration_seconds': conf().get('duration_seconds'),
                'video_resolution': conf().get('video_resolution')
            }
        return self._video_states[user_id]
    
    def set_video_duration(self, user_id, duration: int):
        """设置用户的生成视频时长"""
        state = self.__get_video_state__(user_id)
        state['duration_seconds'] = duration
        return state['duration_seconds']
    
    def set_video_resolution(self, user_id, video_resolution: str):
        """设置用户的生成视频分辨率"""
        state = self.__get_video_state__(user_id)
        state['video_resolution'] = video_resolution
        return state['video_resolution']

    def get_video_duration(self, user_id):
        """获取用户生成视频的时长"""
        return self.__get_video_state__(user_id)['duration_seconds']
    
    def get_video_resolution(self, user_id):
        """获取用户生成视频的分辨率"""
        return self.__get_video_state__(user_id)['video_resolution']
    
    def clear_model_state(self, user_id):
        """清除用户状态"""
        if user_id in self._video_states:
            del self._video_states[user_id]

# 创建全局实例
video_state = UserVideoState()