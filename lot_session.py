"""Bound active API context to a game process and household visit."""
from threading import Lock
from uuid import uuid4


class LotSession:
    def __init__(self):
        self.lock = Lock()
        self.key = None
        self.session = None
        self.started = None

    def update(self, state, fresh):
        with self.lock:
            if not fresh:
                # A stale sample is not proof the player left the lot.
                if state.get('status') != 'lot':
                    self.key = self.session = self.started = None
                return None, None
            key = (state.get('pid'), state.get('currentFamily'))
            if self.key != key:
                self.key, self.session, self.started = key, uuid4().hex, state['sampledUtc']
            return self.session, self.started
