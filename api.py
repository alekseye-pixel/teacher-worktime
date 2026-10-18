"""Клиент JSON API без хранения пароля на диске."""
import json
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPSHandler, ProxyHandler, build_opener
from urllib.parse import urlparse


class ApiError(Exception):
    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


class Api:
    def __init__(self, base='http://127.0.0.1:8765', cafile=None):
        parsed = urlparse(base)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
            raise ApiError('Адрес сервера должен начинаться с http:// или https://')
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ApiError('Адрес сервера не должен содержать учётные данные или параметры')
        if parsed.scheme == 'http' and parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
            raise ApiError('HTTP разрешён только для локального сервера; используйте HTTPS')
        self.base = base.rstrip('/')
        self.token = None
        self.user = None
        self.context = ssl.create_default_context(cafile=cafile)
        handlers = [HTTPSHandler(context=self.context)]
        if parsed.hostname in {'127.0.0.1', 'localhost', '::1'}:
            handlers.insert(0, ProxyHandler({}))
        self.opener = build_opener(*handlers)

    def request(self, route, payload):
        encoded = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        request = Request(self.base + route, encoded, headers, method='POST')
        try:
            with self.opener.open(request, timeout=15) as response:
                result = json.loads(response.read().decode('utf-8'))
        except HTTPError as error:
            try:
                detail = json.loads(error.read().decode('utf-8')).get('error', 'Ошибка сервера')
            except (ValueError, UnicodeDecodeError):
                detail = f'HTTP {error.code}'
            raise ApiError(detail, error.code)
        except (URLError, TimeoutError, OSError) as error:
            raise ApiError('Нет соединения с сервером. Проверьте адрес и запуск сервера.') from error
        except (ValueError, UnicodeDecodeError) as error:
            raise ApiError('Сервер вернул некорректный ответ') from error
        if 'error' in result:
            raise ApiError(result['error'])
        return result['result']

    def login(self, login, password):
        result = self.request('/login', {'login': login, 'password': password})
        self.token = result['token']
        self.user = result['user']
        return self.user

    def call(self, method, data=None):
        return self.request('/rpc', {'method': method, 'data': data or {}})

    def logout(self):
        try:
            if self.token:
                self.call('logout')
        finally:
            self.token = None
            self.user = None
