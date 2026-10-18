"""Предметные правила проекта «Учёт времени». Python 3.12+."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import re
import secrets


ROLES = {"admin": "Администратор", "head": "Руководитель", "teacher": "Преподаватель"}
STATUSES = {"draft": "Черновик", "submitted": "На проверке", "approved": "Согласовано", "returned": "Возвращено"}


class DomainError(Exception):
    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.code = code


def require(condition: bool, message: str, code: int = 400) -> None:
    if not condition:
        raise DomainError(message, code)


def text(value, field="Значение", limit=500, required=True):
    require(isinstance(value, str), f"{field}: ожидается строка")
    result = value.strip()
    require(bool(result) or not required, f"{field}: заполните поле")
    require(len(result) <= limit, f"{field}: не более {limit} символов")
    require(not any(ord(c) < 32 and c not in '\n\t' for c in result), f"{field}: управляющие символы недопустимы")
    return result


def integer(value, field="Число", lower=1, upper=2_147_483_647):
    require(not isinstance(value, bool), f"{field}: ожидается целое число")
    try:
        result = int(str(value))
    except (ValueError, TypeError):
        raise DomainError(f"{field}: ожидается целое число")
    require(lower <= result <= upper, f"{field}: допустимо от {lower} до {upper}")
    return result


def decimal(value, field="Число", lower="0", upper="1000000"):
    try:
        result = Decimal(str(value).replace(',', '.'))
    except InvalidOperation:
        raise DomainError(f"{field}: неверный формат числа")
    require(result.is_finite(), f"{field}: требуется конечное число")
    require(Decimal(lower) <= result <= Decimal(upper), f"{field}: значение вне диапазона")
    return result


def day(value):
    require(isinstance(value, str), "Дата должна иметь формат ГГГГ-ММ-ДД")
    try:
        result = date.fromisoformat(value)
    except ValueError:
        raise DomainError("Некорректная дата; используйте ГГГГ-ММ-ДД")
    require(result.isoformat() == value, "Дата должна иметь формат ГГГГ-ММ-ДД")
    return result


def month(value):
    require(isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}', value), "Период должен иметь формат ГГГГ-ММ")
    day(value + '-01')
    return value


def time_minutes(value):
    require(isinstance(value, str) and re.fullmatch(r'\d{2}:\d{2}', value), "Время должно иметь формат ЧЧ:ММ")
    hour, minute = map(int, value.split(':'))
    require(0 <= hour < 24 and 0 <= minute < 60, "Некорректное время")
    return hour * 60 + minute


def interval(start, end):
    first = time_minutes(start)
    last = time_minutes(end)
    require(last > first, "Окончание должно быть позже начала в пределах одного дня")
    return first, last, last - first


def overlaps(first, last, other_first, other_last):
    return first < other_last and last > other_first


def hours(minutes):
    return str((Decimal(minutes) / Decimal(60)).quantize(Decimal('0.01')))


def units(minutes, unit, academic_minutes=45):
    divisor = academic_minutes if unit == "academic" else 60
    return str((Decimal(minutes) / Decimal(divisor)).quantize(Decimal('0.01')))


def password_hash(password, *, demo=False):
    if not (demo and password == '12345'):
        validate_password(password)
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 260000).hex()
    return f"pbkdf2_sha256$260000${salt}${digest}"


def password_matches(password, encoded):
    try:
        algorithm, iterations, salt, expected = encoded.split('$')
        if algorithm != 'pbkdf2_sha256':
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(iterations)).hex()
        return hmac.compare_digest(expected, actual)
    except (ValueError, TypeError, AttributeError):
        return False


def validate_password(password):
    require(isinstance(password, str), "Пароль должен быть строкой")
    require(10 <= len(password) <= 128, "Пароль должен содержать от 10 до 128 символов")
    require(any(c.isalpha() for c in password) and any(c.isdigit() for c in password), "Пароль должен содержать буквы и цифры")


def transition(current, target, role, owner, reviewer):
    require(current in STATUSES and target in STATUSES, "Неизвестное состояние")
    author_path = current in {'draft', 'returned'} and target == 'submitted'
    review_path = current == 'submitted' and target in {'approved', 'returned'}
    reopen_path = current == 'submitted' and target == 'draft'
    if author_path or reopen_path:
        require(owner == reviewer or role == 'admin', "Передавать и отзывать запись может её автор", 403)
        return
    if review_path:
        require(role in {'head', 'admin'}, "Согласование недоступно для вашей роли", 403)
        require(owner != reviewer, "Согласование собственной записи запрещено", 403)
        return
    raise DomainError("Такой переход состояния не разрешён", 409)


def safe_filename(value):
    result = re.sub(r'[^\w .()-]', '-', value, flags=re.UNICODE).strip(' .')
    return result[:120] or 'Отчёт'


def csv_safe(value):
    result = str(value if value is not None else '')
    if result.startswith(('=', '+', '-', '@', '\t', '\r')):
        return "'" + result
    return result


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')
