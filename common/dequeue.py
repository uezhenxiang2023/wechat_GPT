from queue import Full, Queue
from time import monotonic as time


# add implementation of putleft to Queue
class Dequeue(Queue):
    def __init__(self, maxsize=0):
        super().__init__(maxsize=maxsize)

    def putleft(self, item, block=True, timeout=None):
        # gevent patches queue.Queue to a C extension type with a deque backend
        # and no stdlib condition variables, so use its internal wake-up path.
        if hasattr(self, "queue") and not hasattr(self, "not_full"):
            maxsize = self.maxsize or 0
            if maxsize > 0 and self.qsize() >= maxsize:
                if not block:
                    raise Full
                if timeout is not None and timeout < 0:
                    raise ValueError("'timeout' must be a non-negative number")
                self.put(item, block=block, timeout=timeout)
                self.queue.rotate(1)
                return
            self._putleft(item)
            self._unlock()
            return

        with self.not_full:
            if self.maxsize > 0:
                if not block:
                    if self._qsize() >= self.maxsize:
                        raise Full
                elif timeout is None:
                    while self._qsize() >= self.maxsize:
                        self.not_full.wait()
                elif timeout < 0:
                    raise ValueError("'timeout' must be a non-negative number")
                else:
                    endtime = time() + timeout
                    while self._qsize() >= self.maxsize:
                        remaining = endtime - time()
                        if remaining <= 0.0:
                            raise Full
                        self.not_full.wait(remaining)
            self._putleft(item)
            self.unfinished_tasks += 1
            self.not_empty.notify()

    def putleft_nowait(self, item):
        return self.putleft(item, block=False)

    def _putleft(self, item):
        self.queue.appendleft(item)
