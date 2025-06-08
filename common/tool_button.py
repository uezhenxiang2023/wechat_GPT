"""聊天窗口中的工具状态"""

class UserToolState:
    def __init__(self):
        self._user_states = {}
    
    def get_user_state(self, user_id):
        """获取用户工具状态,如果不存在则创建新的"""
        if user_id not in self._user_states:
            self._user_states[user_id] = {
                'searching': False,
                'imaging': False
            }
        return self._user_states[user_id]
    
    def toggle_search(self, user_id):
        """切换用户的搜索状态"""
        state = self.get_user_state(user_id)
        state['searching'] = not state['searching']
        return state['searching']
    
    def toggle_imaging(self, user_id):
        """切换用户的图像生成状态"""
        state = self.get_user_state(user_id)
        state['imaging'] = not state['imaging']
        return state['imaging']
    
    def get_search_state(self, user_id):
        """获取用户搜索状态"""
        return self.get_user_state(user_id)['searching']
    
    def get_image_state(self, user_id):
        """获取用户图像生成状态"""
        return self.get_user_state(user_id)['imaging']
    
    def clear_user_state(self, user_id):
        """清除用户状态"""
        if user_id in self._user_states:
            del self._user_states[user_id]

# 创建全局实例
tool_state = UserToolState()