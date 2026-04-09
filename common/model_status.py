"""聊天窗口中的模型状态"""

from config import conf

class UserModelState:
    def __init__(self):
        self._model_states = {}
    
    def __get_model_state__(self, user_id):
        """获取用户模型状态,如果不存在则创建新的"""
        if user_id not in self._model_states:
            self._model_states[user_id] = {
                'model': conf().get('model'),
                'text_to_image': conf().get('text_to_image'),
                'image_size': conf().get('image_create_size', '1k'),
                'text_to_voice': conf().get('text_to_voice'),
                'text_to_video': conf().get('text_to_video'),
                'video_mode': conf().get('video_mode', 'FirstLast')
            }
        return self._model_states[user_id]
    
    def toggle_basic_model(self, user_id, model):
        """切换用户的基础模型状态"""
        state = self.__get_model_state__(user_id)
        state['model'] = model
        return state['model']
    
    def toggle_image_model(self, user_id, image_model):
        """切换用户的图像模型状态"""
        state = self.__get_model_state__(user_id)
        state['text_to_image'] = image_model
        return state['text_to_image']

    def toggle_image_size(self, user_id, image_size):
        """切换用户的图片尺寸状态"""
        state = self.__get_model_state__(user_id)
        state['image_size'] = image_size
        return state['image_size']
    
    def toggle_voice_model(self, user_id, voice_model):
        """切换用户的语音模型状态"""
        state = self.__get_model_state__(user_id)
        state['text_to_voice'] = voice_model
        return state['text_to_voice']
    
    def toggle_video_model(self, user_id, video_model):
        """切换用户的视频模型状态"""
        state = self.__get_model_state__(user_id)
        state['text_to_video'] = video_model
        return state['text_to_video']

    def toggle_video_mode(self, user_id, video_mode):
        """切换用户的视频模式状态"""
        state = self.__get_model_state__(user_id)
        state['video_mode'] = video_mode
        return state['video_mode']

    def get_basic_state(self, user_id):
        """获取用户基础模型状态"""
        return self.__get_model_state__(user_id)['model']
    
    def get_image_model(self, user_id):
        """获取用户图像模型状态"""
        return self.__get_model_state__(user_id)['text_to_image']

    def get_image_size(self, user_id):
        """获取用户图片尺寸状态"""
        return self.__get_model_state__(user_id)['image_size']
    
    def get_voice_state(self, user_id):
        """获取用户语音状态"""
        return self.__get_model_state__(user_id)['text_to_voice']
    
    def get_video_state(self, user_id):
        """获取用户视频模型状态"""
        return self.__get_model_state__(user_id)['text_to_video']

    def get_video_mode(self, user_id):
        """获取用户视频模式状态"""
        return self.__get_model_state__(user_id)['video_mode']
    
    def clear_model_state(self, user_id):
        """清除用户状态"""
        if user_id in self._model_states:
            del self._model_states[user_id]

# 创建全局实例
model_state = UserModelState()
