from common.expired_dict import ExpiredDict
from common.log import logger
from config import conf


class Session(object):
    def __init__(self, session_id, system_prompt=None):
        self.session_id = session_id
        self.messages = []
        if system_prompt is None:
            self.system_prompt = conf().get("character_desc", "")
        else:
            self.system_prompt = system_prompt

    # 重置会话
    def reset(self):
        system_item = {"role": "system", "content": self.system_prompt}
        self.messages = [system_item]

    def set_system_prompt(self, system_prompt):
        self.system_prompt = system_prompt
        self.reset()

    def add_query(self, query):
        user_item = {"role": "user", "content": query}
        self.messages.append(user_item)

    def add_reply(self, reply):
        assistant_item = {"role": "assistant", "content": reply}
        self.messages.append(assistant_item)

    def discard_exceeding(self, max_tokens=None, cur_tokens=None):
        raise NotImplementedError

    def calc_tokens(self):
        raise NotImplementedError


class SessionManager(object):
    def __init__(self, sessioncls, **session_args):
        if conf().get("expires_in_seconds"):
            sessions = ExpiredDict(conf().get("expires_in_seconds"))
        else:
            sessions = dict()
        self.sessions = sessions
        self.sessioncls = sessioncls
        self.session_args = session_args

    def build_session(self, session_id, system_prompt=None):
        """
        如果session_id不在sessions中，创建一个新的session并添加到sessions中
        如果system_prompt不会空，会更新session的system_prompt并重置session
        """
        if session_id is None:
            return self.sessioncls(session_id, system_prompt, **self.session_args)

        if session_id not in self.sessions:
            self.sessions[session_id] = self.sessioncls(session_id, system_prompt, **self.session_args)
        elif system_prompt is not None:  # 如果有新的system_prompt，更新并重置session
            self.sessions[session_id].set_system_prompt(system_prompt)
        session = self.sessions[session_id]
        return session

    def session_query(self, query, session_id):
        session = self.build_session(session_id)
        session.add_query(query)
        try:
            max_tokens = conf().get("conversation_max_tokens", 1000)
            total_tokens = session.discard_exceeding(max_tokens, None)
            logger.debug("prompt tokens used={}".format(total_tokens))
        except Exception as e:
            logger.warning("Exception when counting tokens precisely for prompt: {}".format(str(e)))
        return session

    def session_reply(self, reply, session_id, total_tokens=None):
        session = self.build_session(session_id)
        session.add_reply(reply)
        if total_tokens is not None and hasattr(session, 'last_total_tokens'):
            session.last_total_tokens = total_tokens
        try:
            max_tokens = conf().get("conversation_max_tokens", 1000)
            tokens_cnt = session.discard_exceeding(max_tokens, total_tokens)
            logger.debug("raw total_tokens={}, savesession tokens={}".format(total_tokens, tokens_cnt))
        except Exception as e:
            logger.warning("Exception when counting tokens precisely for session: {}".format(str(e)))
        return session
    
    def session_inject_media(self, session_id, media_type, data, source_model, mime_type=None, fps=1):
        """
        将跨模型生成的媒体结果注入当前 session 上下文

        :param session_id:   目标会话 ID
        :param media_type:   'image' | 'video'
        :param data:         base64 编码字符串，或视频链接 URL
        :param source_model: 生成来源，如 const.KLING_V3_OMNI
        :param mime_type:    如 'image/jpeg' 'video/mp4'，None 时自动推断
        :param fps:          视频抽帧频率，取值范围 [0.2, 5]，仅 video 时生效，默认 1
        """
        session = self.build_session(session_id)

        if mime_type is None:
            mime_type = 'video/mp4' if media_type == 'video' else 'image/jpeg'

        if media_type == 'video':
            media_content = {
                "type": "video_url",
                "video_url": {
                    "url": f"data:{mime_type};base64,{data}",
                    "fps": fps
                }
            }
        else:
            media_content = {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime_type};base64,{data}"
                }
            }

        media_block = {
            "role": "assistant",
            "content": [
                media_content,
                {
                    "type": "text",
                    "text": f"[由 {source_model} 生成]"
                }
            ]
        }

        session.messages.append(media_block)

    def clear_session(self, session_id):
        if session_id in self.sessions:
            del self.sessions[session_id]

    def clear_all_session(self):
        self.sessions.clear()
