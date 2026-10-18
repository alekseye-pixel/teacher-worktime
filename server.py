"""API клиент-серверной системы. По умолчанию доступен только на локальном компьютере."""
from __future__ import annotations
import argparse
from collections import defaultdict, deque
import getpass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sqlite3
import ssl
import threading
import time

from domain import DomainError, require
from storage import Store
from maintenance import InstanceLock
import quality


class Application:
    def __init__(self, store, session_seconds=3600):
        self.store = store
        self.session_seconds = session_seconds
        self.sessions = {}
        self.failures = defaultdict(deque)
        self.lock = threading.RLock()

    def login(self, login, password, address):
        current = time.monotonic()
        key = (address, str(login).casefold())
        with self.lock:
            failures = self.failures[key]
            while failures and current - failures[0] > 300:
                failures.popleft()
            require(len(failures) < 10, 'Слишком много попыток; повторите через пять минут', 429)
        try:
            user = self.store.authenticate(login, password)
        except DomainError:
            with self.lock:
                failures.append(current)
            raise
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.failures.pop(key, None)
            self.sessions = {k: v for k, v in self.sessions.items() if current - v['created'] < self.session_seconds}
            self.sessions[token] = {'user_id': user['id'], 'created': current}
        return {'token': token, 'user': user, 'expires_in': self.session_seconds}

    def resolve(self, token):
        with self.lock:
            session = self.sessions.get(token)
            if session is None or time.monotonic() - session['created'] >= self.session_seconds:
                self.sessions.pop(token, None)
                raise DomainError('Сеанс завершён; войдите снова', 401)
        return self.store.user(session['user_id'])

    def revoke_user(self, user_id):
        with self.lock:
            self.sessions = {token: row for token, row in self.sessions.items() if row['user_id'] != user_id}

    def call(self, token, method, data):
        user = self.resolve(token)
        require(isinstance(data, dict), 'Параметры должны быть объектом JSON')
        handlers = {
            'me': lambda: user,
            'catalogs': lambda: self.store.catalogs(user, data.get('catalog')),
            'catalog.save': lambda: self.store.catalog_save(user, data.get('catalog'), data.get('record', {})),
            'settings': lambda: self.store.settings(user),
            'settings.save': lambda: self.store.settings_save(user, data),
            'entries': lambda: self.store.entries(user, data),
            'entry.save': lambda: self.store.entry_save(user, data),
            'entry.delete': lambda: self.store.entry_delete(user, data.get('id'), data.get('version')),
            'entry.transition': lambda: self.store.entry_transition(user, data),
            'entry.history': lambda: self.store.history(user, data.get('id')),
            'plans': lambda: self.store.plans(user, data.get('period')),
            'plan.save': lambda: self.store.plan_save(user, data),
            'report': lambda: self.store.report(user, data.get('period')),
            'dashboard': lambda: self.store.dashboard(user, data.get('period')),
            'period.set': lambda: self.store.period_set(user, data),
            'users': lambda: self.store.user_list(user),
            'audit': lambda: self.store.audit(user, data.get('limit', 300)),
            'feedback': lambda: self.store.feedback(user),
            'feedback.save': lambda: self.store.feedback_save(user, data),
            'import': lambda: self.store.import_entries(user, data.get('rows'), data.get('dry_run', True)),
            'backup': lambda: self.store.backup(user),
            'quality': lambda: quality.findings(self.store.report(user, data.get('period'))),
            'calendar': lambda: quality.calendar(self.store.report(user, data.get('period'))),
            'search': lambda: quality.search(self.store.report(user, data.get('period')), data.get('query', ''), data.get('status', ''), data.get('type_id', '')),
        }
        if method == 'logout':
            with self.lock:
                self.sessions.pop(token, None)
            return {'logged_out': True}
        if method == 'password':
            result = self.store.password_change(user, data.get('old'), data.get('new'))
            self.revoke_user(user['id'])
            return result
        if method == 'user.save':
            result = self.store.user_save(user, data)
            if data.get('id'):
                self.revoke_user(int(data['id']))
            return result
        require(method in handlers, 'Неизвестная операция', 404)
        return handlers[method]()


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'Worktime/1.0'
    sys_version = ''

    def send_json(self, code, payload):
        encoded = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(encoded)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(encoded)
        self.close_connection = True

    def do_GET(self):
        if self.path == '/health':
            self.send_json(200, {'service': 'worktime', 'version': '1.0', 'status': 'ok'})
        else:
            self.send_json(404, {'error': 'Ресурс не найден'})

    def do_POST(self):
        try:
            require(self.path in {'/login', '/rpc'}, 'Ресурс не найден', 404)
            require(self.headers.get('Content-Type', '').split(';')[0] == 'application/json', 'Ожидается application/json', 415)
            try:
                size = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                raise DomainError('Некорректная длина запроса')
            require(0 < size <= 1_048_576, 'Превышен размер запроса', 413)
            self.connection.settimeout(15)
            raw = self.rfile.read(size)
            require(len(raw) == size, 'Запрос получен не полностью')
            payload = json.loads(raw.decode('utf-8'))
            require(isinstance(payload, dict), 'Ожидается объект JSON')
            if self.path == '/login':
                result = self.server.application.login(payload.get('login'), payload.get('password'), self.client_address[0])
            else:
                authorization = self.headers.get('Authorization', '')
                require(authorization.startswith('Bearer '), 'Требуется вход в систему', 401)
                token = authorization[7:]
                result = self.server.application.call(token, payload.get('method'), payload.get('data', {}))
            self.send_json(200, {'result': result})
        except DomainError as error:
            self.send_json(error.code, {'error': str(error)})
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json(400, {'error': 'Некорректный JSON'})
        except sqlite3.IntegrityError:
            self.send_json(409, {'error': 'Нарушена уникальность или ссылка на запись справочника'})
        except (TypeError, ValueError, KeyError):
            self.send_json(400, {'error': 'Некорректные параметры операции'})
        except Exception:
            self.log_error('Внутренняя ошибка; проверьте серверный журнал')
            self.send_json(500, {'error': 'Внутренняя ошибка сервера'})

    def log_message(self, format, *args):
        # Токены, пароли и содержимое тел запросов не записываются в HTTP-журнал.
        super().log_message(format, *args)


def make_server(store, host='127.0.0.1', port=8765):
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.application = Application(store)
    return server


def main():
    parser = argparse.ArgumentParser(description='Сервер учёта рабочего времени')
    parser.add_argument('--db', default='data/worktime.sqlite')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--demo', action='store_true', help='Создать учебные данные в пустой базе')
    parser.add_argument('--init', action='store_true', help='Создать начального администратора')
    parser.add_argument('--cert', help='PEM сертификат для HTTPS')
    parser.add_argument('--key', help='Закрытый ключ HTTPS')
    args = parser.parse_args()
    require((args.cert is None) == (args.key is None), 'Укажите одновременно сертификат и ключ')
    require(args.host in {'127.0.0.1', 'localhost', '::1'} or bool(args.cert), 'Для сетевого доступа требуется HTTPS и доверенный сертификат')
    instance_lock = InstanceLock(args.db)
    instance_lock.__enter__()
    store = Store(args.db)
    if args.demo:
        store.demo()
        print('Учебный вход admin: 12345. Пользователи head, teacher, teacher2: DemoTime2026!')
    if args.init:
        password = getpass.getpass('Пароль первоначального администратора: ')
        require(password == getpass.getpass('Повторите пароль: '), 'Пароли не совпадают')
        store.bootstrap(password)
    server = make_server(store, args.host, args.port)
    if args.cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(args.cert, args.key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    scheme = 'https' if args.cert else 'http'
    print(f'Сервер: {scheme}://{args.host}:{args.port}; база: {store.path}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('Сервер остановлен')
    finally:
        server.server_close()
        instance_lock.__exit__(None, None, None)


if __name__ == '__main__':
    main()
