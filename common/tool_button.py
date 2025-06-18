"""聊天窗口中的工具状态"""

class UserToolState:
    def __init__(self):
        self._user_states = {}
    
    def get_user_state(self, user_id):
        """获取用户工具状态,如果不存在则创建新的"""
        if user_id not in self._user_states:
            self._user_states[user_id] = {
                'searching': False,
                'imaging': False,
                'printing': False,
                'breakdowning': False
            }
        return self._user_states[user_id]
    
    def toggle_searching(self, user_id):
        """切换用户的搜索状态"""
        state = self.get_user_state(user_id)
        state['searching'] = not state['searching']
        return state['searching']
    
    def toggle_imaging(self, user_id):
        """切换用户的图像生成状态"""
        state = self.get_user_state(user_id)
        state['imaging'] = not state['imaging']
        return state['imaging']
    
    def toggle_printing(self, user_id):
        """切换剧本排版状态"""
        state = self.get_user_state(user_id)
        state['printing'] = not state['printing']
        return state['printing']
    
    def toggle_breakdowning(self, user_id):
        """切换顺分场表状态"""
        state = self.get_user_state(user_id)
        state['breakdowning'] = not state['breakdowning']
        return state['breakdowning']

    def get_search_state(self, user_id):
        """获取用户搜索状态"""
        return self.get_user_state(user_id)['searching']
    
    def get_image_state(self, user_id):
        """获取用户图像生成状态"""
        return self.get_user_state(user_id)['imaging']
    
    def get_print_state(self, user_id):
        """获取剧本排版状态"""
        return self.get_user_state(user_id)['printing']
    
    def get_breakdown_state(self, user_id):
        """获取顺分场状态"""
        return self.get_user_state(user_id)['breakdowning']

    def set_printing(self, user_id, status:bool):
        """设置剧本排版状态"""
        state = self.get_user_state(user_id)
        state['printing'] = status
        return state['printing']
    
    def clear_user_state(self, user_id):
        """清除用户状态"""
        if user_id in self._user_states:
            del self._user_states[user_id]

# 创建全局实例
tool_state = UserToolState()