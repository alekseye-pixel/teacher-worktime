"""Восстановление при остановленном сервере; прежняя база сохраняется."""
import argparse
from datetime import datetime
import os
from pathlib import Path
import sqlite3
import tempfile
from domain import require
from maintenance import InstanceLock, verify_backup


def restore(source, target):
    source = Path(source).resolve()
    target = Path(target).resolve()
    require(source != target, 'Источник и целевая база должны различаться')
    report = verify_backup(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    with InstanceLock(target):
        previous = None
        if target.exists():
            previous = target.with_name(target.stem + '-до-восстановления-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.sqlite')
            old = sqlite3.connect(target)
            copy = sqlite3.connect(previous)
            try:
                old.backup(copy)
            finally:
                copy.close()
                old.close()
        handle, name = tempfile.mkstemp(dir=target.parent, suffix='.sqlite')
        os.close(handle)
        temporary = Path(name)
        try:
            src = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
            dst = sqlite3.connect(temporary)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            verify_backup(temporary)
            for suffix in ('-wal', '-shm'):
                Path(str(target) + suffix).unlink(missing_ok=True)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return {'restored': str(target), 'previous': str(previous) if previous else None, **report}


def main():
    parser = argparse.ArgumentParser(description='Восстановление базы из резервной копии')
    parser.add_argument('source')
    parser.add_argument('--db', default='data/worktime.sqlite')
    args = parser.parse_args()
    verify_backup(args.source)
    print('Остановите сервер. Текущая база будет сохранена отдельным файлом.')
    require(input('Для восстановления введите ВОССТАНОВИТЬ: ') == 'ВОССТАНОВИТЬ', 'Восстановление отменено')
    print(restore(args.source, args.db))


if __name__ == '__main__':
    main()
