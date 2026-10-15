"""Shared per-device session guard, including fallback attempts from other workers."""
from contextlib import contextmanager
import threading
from ..process import ProbeSkipped


class DeviceSlots:
    def __init__(self):
        self.condition = threading.Condition()
        self.active = {}

    @contextmanager
    def claim(self, device, limit, cancel):
        with self.condition:
            while self.active.get(device, 0) >= limit:
                if cancel.is_set():
                    raise ProbeSkipped('Stopped while waiting for a GPU session')
                self.condition.wait(.1)
            if cancel.is_set():
                raise ProbeSkipped('Stopped before acquiring a GPU session')
            self.active[device] = self.active.get(device, 0) + 1
        try:
            yield
        finally:
            with self.condition:
                self.active[device] -= 1
                self.condition.notify_all()
