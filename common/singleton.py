def singleton(cls):
    instances = {}

    def get_instance(session_id=None, *args, **kwargs):
        if session_id not in instances:
            instances[session_id] = cls(session_id, *args, **kwargs)
        return instances[session_id]

    return get_instance
