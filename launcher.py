"""Запуск учебной программы одним файлом без отдельного запуска сервера."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import traceback
from urllib.request import ProxyHandler, build_opener

from domain import DomainError
from maintenance import InstanceLock
from process_group import ProcessGroup


class AlreadyRunning(Exception):
    pass


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def needs_demo(database):
    if not database.exists():
        return True
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as connection:
        return connection.execute('SELECT count(*) FROM users').fetchone()[0] == 0


def show_error(message):
    from tk_support import configure_tk
    configure_tk()
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror('Учёт рабочего времени', message, parent=root)
    root.destroy()


def run(bundle, data_dir, test_report=None):
    data_dir.mkdir(parents=True, exist_ok=True)
    state_file = data_dir / 'Состояние запуска.json'
    destination = test_report or state_file
    database = data_dir / 'worktime.sqlite'
    server_dir = bundle / ('Сервер' if (bundle / 'Сервер').is_dir() else 'Сервер учёта')
    client_dir = bundle / ('Клиент' if (bundle / 'Клиент').is_dir() else 'Учёт времени')
    server_exe = server_dir / 'Сервер учёта.exe'
    client_exe = client_dir / 'Учёт времени.exe'
    report = {'started_utc': datetime.now(timezone.utc).isoformat(),
              'launcher': sys.executable, 'frozen': bool(getattr(sys, 'frozen', False)),
              'database': str(database), 'status': 'starting', 'job_cleanup': False,
              'launcher_pid': os.getpid()}
    processes = []
    group = None
    lock = None
    stream = None
    result_code = 1
    try:
        if not server_exe.is_file() or not client_exe.is_file():
            raise RuntimeError('Файлы программы не найдены. Распакуйте архив полностью вместе с папками «Клиент» и «Сервер».')
        lock = InstanceLock(data_dir / 'launcher')
        try:
            lock.__enter__()
        except DomainError as error:
            lock = None
            raise AlreadyRunning('Программа уже открыта. Используйте открытое окно клиента.') from error
        with InstanceLock(database):
            pass
        demo = needs_demo(database)
        group = ProcessGroup()
        report['job_cleanup'] = True
        journal = data_dir / 'Журналы'
        journal.mkdir(exist_ok=True)
        server_log = journal / ('Сервер ' + datetime.now().strftime('%Y-%m-%d %H-%M-%S') + '.log')
        stream = server_log.open('wb')
        environment = os.environ.copy()
        for variable in ['TCL_LIBRARY', 'TK_LIBRARY', 'PYTHONHOME', 'PYTHONPATH']:
            environment.pop(variable, None)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        command = [str(server_exe), '--db', str(database), '--port', str(port)]
        if demo:
            command.append('--demo')
        server = subprocess.Popen(command, cwd=data_dir, env=environment, stdout=stream,
                                  stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append(server)
        group.add(server)
        report.update({'server_pid': server.pid, 'server_url': url, 'demo_created': demo,
                       'server_log': str(server_log)})
        deadline = time.monotonic() + 30
        health_opener = build_opener(ProxyHandler({}))
        while True:
            if server.poll() is not None:
                raise RuntimeError('Сервер не запустился. Подробности в журнале: ' + str(server_log))
            try:
                with health_opener.open(url + '/health', timeout=.5) as response:
                    health = json.load(response)
                if health.get('service') == 'worktime' and health.get('status') == 'ok' and server.poll() is None:
                    break
            except (OSError, ValueError) as error:
                report['last_connection_error'] = str(error)
            if time.monotonic() >= deadline:
                raise RuntimeError('Сервер не ответил вовремя. Журнал: ' + str(server_log))
            time.sleep(.1)
        client_command = [str(client_exe), '--server', url]
        client_report = None
        if test_report:
            client_report = test_report.with_name(test_report.stem + '-client.json')
            client_command += ['--self-test', str(client_report)]
        client = subprocess.Popen(client_command, cwd=data_dir, env=environment,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        processes.append(client)
        group.add(client)
        report.update({'client_pid': client.pid, 'status': 'running'})
        write_report(destination, report)
        if not test_report:
            write_report(state_file, report)
        started = time.monotonic()
        while client.poll() is None:
            if server.poll() is not None:
                raise RuntimeError('Сервер завершил работу. Журнал: ' + str(server_log))
            if test_report and time.monotonic() - started > 75:
                raise TimeoutError('Проверка клиента не завершилась вовремя')
            time.sleep(.1)
        if client.returncode:
            raise RuntimeError('Клиент завершился с ошибкой. Код: ' + str(client.returncode))
        if client_report:
            gui = json.loads(client_report.read_text(encoding='utf-8'))
            if gui.get('status') != 'passed' or len(gui.get('forms', [])) != 19:
                raise RuntimeError('Проверка форм клиента не пройдена')
            report['forms_checked'] = len(gui['forms'])
        report['status'] = 'passed' if test_report else 'closed'
        result_code = 0
    except AlreadyRunning as error:
        report.update({'status': 'already_running', 'message': str(error)})
        result_code = 2
        if not test_report:
            show_error(str(error))
    except Exception as error:
        report.update({'status': 'failed', 'message': str(error), 'traceback': traceback.format_exc()})
        if not test_report:
            show_error(str(error))
    finally:
        if group:
            group.close()
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        report['children_stopped'] = all(process.poll() is not None for process in processes)
        if stream:
            stream.close()
        if lock:
            lock.__exit__(None, None, None)
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        if test_report or report['status'] != 'already_running':
            write_report(destination, report)
    return result_code


def main():
    default_bundle = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description='Учёт рабочего времени преподавателя')
    parser.add_argument('--bundle', type=Path, default=default_bundle)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--self-test', type=Path)
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    data_dir = (args.data_dir or bundle / 'Данные').resolve()
    try:
        return run(bundle, data_dir, args.self_test.resolve() if args.self_test else None)
    except OSError as error:
        if args.self_test:
            raise
        show_error('Не удалось открыть папку данных программы. Распакуйте комплект в доступную для записи папку.\n\n' + str(error))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
