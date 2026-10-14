import json
import os
import threading
from pathlib import Path


def data_dir():
    base = Path(os.environ.get('APPDATA', Path.home() / '.config')) / 'EdiusBooster'
    base.mkdir(parents=True, exist_ok=True)
    return base


class Settings:
    def __init__(self):
        self.path = data_dir() / 'settings.json'
        self.lock = threading.RLock()
        try:
            loaded = json.loads(self.path.read_text(encoding='utf-8'))
            self.values = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            self.values = {}

    def get(self, key, default=None):
        with self.lock:
            return self.values.get(key, default)

    def set(self, key, value):
        with self.lock:
            self.values[key] = value
            temp = self.path.with_suffix('.tmp')
            temp.write_text(json.dumps(self.values, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(self.path)
