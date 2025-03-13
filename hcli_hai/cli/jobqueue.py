import queue as q


# Singleton Job Queue for sequential streaming jobs in a single process multithread environment
class JobQueue:
    instance = None
    _queue = None
    _dirty = False

    def __new__(self):
        if self.instance is None:

            self.instance = super().__new__(self)
            self._queue = q.Queue()

        return self.instance

    # adds a named job to the queue if the queue isn't dirty
    def put(self, function):
        if not self._dirty:
            return self._queue.put(function)

    def get(self):
        return self._queue.get(self)

    def qsize(self):
        return self._queue.qsize()

    # we allow for self correction on a dirty queue if we notice the queue is empty
    def empty(self):
        empty = self._queue.empty()
        if empty:
            self._dirty = False
        return empty

    def clear(self):
        self._dirty = False
        return self._queue.queue.clear()

    def list(self):
        return list(self._queue.queue)

    def is_dirty(self):
        return self._dirty

    # dirty status toggle
    def dirty(self, is_dirty):
        self._dirty = is_dirty
        return self._dirty
