"""公开演示：每个 Streamlit 会话一个内存数据库，不创建共享访客数据文件。"""
import sqlite3
import threading
from contextlib import contextmanager
from .repository import Repository, SCHEMA


class SessionRepository(Repository):
    def __init__(self):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(':memory:', check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute('PRAGMA foreign_keys=ON')
        with self.connection() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connection(self):
        with self._lock:
            with self._db:
                yield self._db

    def close(self):
        with self._lock:
            self._db.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
