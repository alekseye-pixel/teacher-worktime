"""Блокировка экземпляра и проверка резервной базы."""
from contextlib import AbstractContextManager
from pathlib import Path
import os
import sqlite3
from domain import DomainError, require


class InstanceLock(AbstractContextManager):
    def __init__(self, database):
        self.path = Path(str(Path(database).resolve()) + '.lock')
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = open(self.path, 'a+b')
        try:
            # Windows запрещает чтение чужого заблокированного байта.
            # Для определения пустого файла достаточно его размера.
            if self.stream.seek(0, os.SEEK_END) == 0:
                self.stream.write(b'0')
                self.stream.flush()
            self.stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            self.stream = None
            raise DomainError('База используется сервером или другим процессом обслуживания')
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.stream:
            try:
                self.stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            finally:
                self.stream.close()
                self.stream = None
        return False


def verify_backup(path):
    source = Path(path).resolve()
    require(source.is_file(), 'Файл резервной копии не найден')
    connection = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
    try:
        integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
        require(integrity == 'ok', 'Резервная копия повреждена')
        require(not connection.execute('PRAGMA foreign_key_check').fetchall(), 'В копии нарушены внешние ключи')
        require(connection.execute('PRAGMA user_version').fetchone()[0] == 1, 'Версия схемы не поддерживается')
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        expected = {'users', 'teachers', 'departments', 'entries', 'worktypes', 'plans', 'decisions', 'periods', 'audit', 'feedback', 'settings'}
        require(expected <= tables, 'Копия не является базой приложения')
        return {'integrity': integrity, 'tables': len(tables), 'size': source.stat().st_size}
    finally:
        connection.close()
