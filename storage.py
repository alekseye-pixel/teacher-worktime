"""SQL-хранилище и операции предметной области. Все проверки выполняются на сервере."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import threading

from domain import DomainError, require, text, integer, decimal, day, month, interval
from domain import overlaps, hours, units, now, password_hash, password_matches, ROLES, transition


class Store:
    CATALOGS = {
        'departments': ('name', 'active'),
        'teachers': ('name', 'department_id', 'position', 'rate', 'active'),
        'worktypes': ('name', 'unit', 'active'),
    }

    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.initialize()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=15000')
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self):
        with self.connect() as db:
            db.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
            db.execute('PRAGMA journal_mode=WAL')
            defaults = {
                'organization': 'МУИВ — проектный пример',
                'academic_minutes': '45',
                'weekly_hours': '36',
                'report_note': 'Проектные данные. Не является официальным табелем университета.',
                'contact': 'Обращения направляются через форму поддержки приложения',
            }
            db.executemany('INSERT OR IGNORE INTO settings(name,value) VALUES(?,?)', defaults.items())

    def bootstrap(self, password):
        with self.connect() as db:
            require(db.execute('SELECT count(*) FROM users').fetchone()[0] == 0, 'Начальная настройка уже выполнена', 409)
            digest = password_hash(password)
            db.execute('INSERT INTO users(login,password,role) VALUES(?,?,?)', ('admin', digest, 'admin'))
            self.log(db, None, 'bootstrap', 'users', 1, 'Создан первоначальный администратор')

    def log(self, db, user, event, entity, entity_id, details):
        actor = user['id'] if user else None
        payload = json.dumps(details, ensure_ascii=False) if isinstance(details, (list, dict)) else str(details)
        db.execute('INSERT INTO audit(actor_id,event,entity,entity_id,details,created_at) VALUES(?,?,?,?,?,?)',
                   (actor, event, entity, str(entity_id), payload, now()))

    def authenticate(self, login, password):
        name = text(login, 'Логин', 80)
        with self.connect() as db:
            row = db.execute('SELECT * FROM users WHERE login=? AND active=1', (name,)).fetchone()
            encoded = row['password'] if row else password_hash('DummyPassword493!')
            valid = password_matches(str(password), encoded)
            require(row is not None and valid, 'Неверный логин или пароль', 401)
            user = dict(row)
            user.pop('password')
            self.log(db, user, 'login', 'users', user['id'], 'Успешный вход')
            return user

    def user(self, user_id):
        with self.connect() as db:
            row = db.execute('SELECT id,login,role,teacher_id,department_id,active FROM users WHERE id=?', (user_id,)).fetchone()
            require(row is not None and row['active'], 'Учётная запись отключена', 401)
            return dict(row)

    def admin(self, user):
        require(user['role'] == 'admin', 'Требуются права администратора', 403)

    def can_teacher(self, db, user, teacher_id):
        teacher = db.execute('SELECT * FROM teachers WHERE id=?', (teacher_id,)).fetchone()
        require(teacher is not None, 'Преподаватель не найден', 404)
        if user['role'] == 'teacher':
            require(user['teacher_id'] == teacher_id, 'Доступ к данным другого преподавателя запрещён', 403)
        if user['role'] == 'head':
            require(user['department_id'] == teacher['department_id'], 'Доступ к другой кафедре запрещён', 403)
        return teacher

    def can_edit(self, db, user, teacher_id):
        self.can_teacher(db, user, teacher_id)
        require(user['role'] == 'admin' or user['teacher_id'] == teacher_id, 'Изменять запись может её автор', 403)

    def is_open(self, db, period):
        row = db.execute('SELECT closed FROM periods WHERE period=?', (period,)).fetchone()
        require(row is None or not row['closed'], 'Период закрыт для изменений', 409)

    def scope(self, user, alias='t'):
        if user['role'] == 'teacher':
            return f' AND {alias}.id=?', [user['teacher_id'] or -1]
        if user['role'] == 'head':
            return f' AND {alias}.department_id=?', [user['department_id'] or -1]
        return '', []

    def catalogs(self, user, catalog):
        require(catalog in self.CATALOGS, 'Неизвестный справочник')
        with self.connect() as db:
            if catalog == 'teachers':
                condition, args = self.scope(user)
                query = 'SELECT t.*,d.name department FROM teachers t JOIN departments d ON d.id=t.department_id WHERE 1=1'
                return [dict(r) for r in db.execute(query + condition + ' ORDER BY t.name,t.id', args)]
            if catalog == 'departments' and user['role'] != 'admin':
                ident = user['department_id']
                if user['teacher_id']:
                    teacher = db.execute('SELECT department_id FROM teachers WHERE id=?', (user['teacher_id'],)).fetchone()
                    ident = teacher['department_id'] if teacher else -1
                return [dict(r) for r in db.execute('SELECT * FROM departments WHERE id=?', (ident or -1,))]
            return [dict(r) for r in db.execute(f'SELECT * FROM {catalog} ORDER BY name,id')]

    def catalog_save(self, user, catalog, data):
        self.admin(user)
        require(catalog in self.CATALOGS, 'Неизвестный справочник')
        values = {'name': text(data.get('name'), 'Наименование', 160)}
        values['active'] = integer(data.get('active', 1), 'Активность', 0, 1)
        if catalog == 'teachers':
            values['department_id'] = integer(data.get('department_id'), 'Кафедра')
            values['position'] = text(data.get('position'), 'Должность', 100)
            values['rate'] = str(decimal(data.get('rate', '1'), 'Ставка', '0.05', '2'))
        if catalog == 'worktypes':
            require(data.get('unit') in {'clock', 'academic'}, 'Выберите единицу измерения')
            values['unit'] = data['unit']
        with self.connect() as db:
            ident = data.get('id')
            if ident:
                ident = integer(ident, 'Код записи')
                require(db.execute(f'SELECT id FROM {catalog} WHERE id=?', (ident,)).fetchone(), 'Запись не найдена', 404)
                if catalog == 'worktypes':
                    old = db.execute('SELECT unit FROM worktypes WHERE id=?', (ident,)).fetchone()
                    used = db.execute('SELECT 1 FROM entries WHERE type_id=? UNION SELECT 1 FROM plans WHERE type_id=?', (ident, ident)).fetchone()
                    require(not used or old['unit'] == values['unit'], 'Нельзя менять единицу уже используемого вида работ', 409)
                fields = ','.join(f'{k}=?' for k in values)
                db.execute(f'UPDATE {catalog} SET {fields} WHERE id=?', [*values.values(), ident])
            else:
                names = ','.join(values)
                placeholders = ','.join('?' for _ in values)
                cursor = db.execute(f'INSERT INTO {catalog}({names}) VALUES({placeholders})', list(values.values()))
                ident = cursor.lastrowid
            self.log(db, user, 'save', catalog, ident, values)
            return {'id': ident}

    def settings(self, user):
        with self.connect() as db:
            return dict(db.execute('SELECT name,value FROM settings'))

    def settings_save(self, user, data):
        self.admin(user)
        require(set(data) <= {'organization', 'academic_minutes', 'weekly_hours', 'report_note', 'contact'}, 'Неизвестная настройка')
        checked = {}
        for key, value in data.items():
            if key == 'academic_minutes':
                checked[key] = str(integer(value, 'Академический час', 30, 60))
            elif key == 'weekly_hours':
                checked[key] = str(decimal(value, 'Недельная продолжительность', '1', '40'))
            else:
                checked[key] = text(value, 'Настройка', 500)
        with self.connect() as db:
            if 'academic_minutes' in checked:
                count = db.execute('SELECT count(*) FROM entries').fetchone()[0]
                old = db.execute("SELECT value FROM settings WHERE name='academic_minutes'").fetchone()[0]
                require(not count or checked['academic_minutes'] == old, 'Изменение длительности академического часа после ввода факта требует миграции данных', 409)
            db.executemany('UPDATE settings SET value=? WHERE name=?', [(v, k) for k, v in checked.items()])
            self.log(db, user, 'settings', 'settings', '', checked)
        return {'saved': True}

    def entries(self, user, filters):
        condition, args = self.scope(user)
        query = '''SELECT e.*,t.name teacher,d.name department,w.name worktype,w.unit
                   FROM entries e JOIN teachers t ON t.id=e.teacher_id
                   JOIN departments d ON d.id=t.department_id JOIN worktypes w ON w.id=e.type_id WHERE 1=1'''
        if filters.get('period'):
            condition += ' AND substr(e.workday,1,7)=?'
            args.append(month(filters['period']))
        if filters.get('status'):
            require(filters['status'] in {'draft', 'submitted', 'approved', 'returned'}, 'Некорректное состояние')
            condition += ' AND e.status=?'
            args.append(filters['status'])
        if filters.get('teacher_id'):
            condition += ' AND e.teacher_id=?'
            args.append(integer(filters['teacher_id']))
        with self.connect() as db:
            rows = db.execute(query + condition + ' ORDER BY e.workday DESC,e.start,e.id', args)
            return [dict(r) for r in rows]

    def prepare_entry(self, db, user, data):
        teacher_id = integer(data.get('teacher_id'), 'Преподаватель')
        teacher = self.can_teacher(db, user, teacher_id)
        self.can_edit(db, user, teacher_id)
        require(teacher['active'], 'Преподаватель неактивен')
        workday = day(data.get('workday'))
        require(workday <= date.today(), 'Факт нельзя ввести будущей датой')
        self.is_open(db, workday.strftime('%Y-%m'))
        first, last, minutes = interval(data.get('start'), data.get('end'))
        type_id = integer(data.get('type_id'), 'Вид работ')
        kind = db.execute('SELECT * FROM worktypes WHERE id=? AND active=1', (type_id,)).fetchone()
        require(kind is not None, 'Выберите действующий вид работ')
        values = {
            'teacher_id': teacher_id,
            'workday': workday.isoformat(),
            'start': data['start'],
            'end': data['end'],
            'minutes': minutes,
            'type_id': type_id,
            'title': text(data.get('title'), 'Содержание работы', 300),
            'evidence': text(data.get('evidence', ''), 'Основание', 500, False),
        }
        ident = integer(data['id']) if data.get('id') else 0
        for other in db.execute('SELECT id,start,end FROM entries WHERE teacher_id=? AND workday=? AND id<>?', (teacher_id, values['workday'], ident)):
            other_first, other_last, _ = interval(other['start'], other['end'])
            require(not overlaps(first, last, other_first, other_last), f"Пересечение с записью №{other['id']}", 409)
        return values

    def entry_save(self, user, data):
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            ident = data.get('id')
            if ident:
                ident = integer(ident)
                old = db.execute('SELECT * FROM entries WHERE id=?', (ident,)).fetchone()
                require(old is not None, 'Запись не найдена', 404)
                self.can_edit(db, user, old['teacher_id'])
                self.is_open(db, old['workday'][:7])
                require(old['status'] in {'draft', 'returned'}, 'Согласованную или переданную запись редактировать нельзя', 409)
                require(old['version'] == integer(data.get('version')), 'Запись изменена другим пользователем; обновите список', 409)
                require(old['teacher_id'] == integer(data.get('teacher_id')), 'Изменение автора записи запрещено')
            values = self.prepare_entry(db, user, data)
            if ident:
                fields = ','.join(f'{key}=?' for key in values)
                db.execute(f'UPDATE entries SET {fields},updated_at=?,version=version+1 WHERE id=?', [*values.values(), now(), ident])
            else:
                names = ','.join(values)
                placeholders = ','.join('?' for _ in values)
                cursor = db.execute(f'INSERT INTO entries({names},created_by,updated_at) VALUES({placeholders},?,?)', [*values.values(), user['id'], now()])
                ident = cursor.lastrowid
            self.log(db, user, 'save', 'entries', ident, values)
            return {'id': ident}

    def entry_delete(self, user, ident, version):
        ident = integer(ident)
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM entries WHERE id=?', (ident,)).fetchone()
            require(row is not None, 'Запись не найдена', 404)
            self.can_edit(db, user, row['teacher_id'])
            self.is_open(db, row['workday'][:7])
            require(row['status'] == 'draft', 'Удалять можно только черновики', 409)
            require(row['version'] == integer(version), 'Запись изменена; обновите список', 409)
            require(not db.execute('SELECT 1 FROM decisions WHERE entry_id=?', (ident,)).fetchone(), 'Запись с историей согласований удалять нельзя; исправьте и передайте повторно', 409)
            self.log(db, user, 'delete', 'entries', ident, dict(row))
            db.execute('DELETE FROM entries WHERE id=?', (ident,))
        return {'deleted': True}

    def entry_transition(self, user, data):
        ident = integer(data.get('id'))
        target = text(data.get('target'), 'Состояние', 20)
        reason = text(data.get('reason', ''), 'Комментарий', 500, False)
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM entries WHERE id=?', (ident,)).fetchone()
            require(row is not None, 'Запись не найдена', 404)
            self.can_teacher(db, user, row['teacher_id'])
            self.is_open(db, row['workday'][:7])
            require(row['version'] == integer(data.get('version')), 'Запись изменена; обновите список', 409)
            owner = db.execute('SELECT id FROM users WHERE teacher_id=? AND active=1', (row['teacher_id'],)).fetchone()
            owner_id = owner['id'] if owner else row['created_by']
            transition(row['status'], target, user['role'], owner_id, user['id'])
            require(target != 'returned' or bool(reason), 'При возврате укажите причину')
            db.execute('UPDATE entries SET status=?,version=version+1,updated_at=? WHERE id=?', (target, now(), ident))
            db.execute('INSERT INTO decisions(entry_id,actor_id,previous,target,reason,created_at) VALUES(?,?,?,?,?,?)',
                       (ident, user['id'], row['status'], target, reason, now()))
            self.log(db, user, 'transition', 'entries', ident, {'from': row['status'], 'to': target, 'reason': reason})
        return {'id': ident, 'status': target}

    def history(self, user, ident):
        ident = integer(ident)
        with self.connect() as db:
            row = db.execute('SELECT teacher_id FROM entries WHERE id=?', (ident,)).fetchone()
            require(row, 'Запись не найдена', 404)
            self.can_teacher(db, user, row['teacher_id'])
            return [dict(r) for r in db.execute('SELECT d.*,u.login actor FROM decisions d JOIN users u ON u.id=d.actor_id WHERE entry_id=? ORDER BY d.id', (ident,))]

    def plans(self, user, period):
        period = month(period)
        scope, args = self.scope(user)
        sql = '''SELECT p.*,t.name teacher,w.name worktype,w.unit FROM plans p
                 JOIN teachers t ON t.id=p.teacher_id JOIN worktypes w ON w.id=p.type_id
                 WHERE p.period=?'''
        with self.connect() as db:
            return [dict(r) for r in db.execute(sql + scope + ' ORDER BY t.name,w.name', [period, *args])]

    def plan_save(self, user, data):
        require(user['role'] in {'head', 'admin'}, 'План редактирует руководитель', 403)
        teacher_id = integer(data.get('teacher_id'))
        period = month(data.get('period'))
        type_id = integer(data.get('type_id'))
        amount = str(decimal(data.get('amount'), 'План', '0', '10000'))
        note = text(data.get('note', ''), 'Примечание', 500, False)
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.can_teacher(db, user, teacher_id)
            self.is_open(db, period)
            require(db.execute('SELECT 1 FROM worktypes WHERE id=? AND active=1', (type_id,)).fetchone(), 'Вид работы не найден')
            old = db.execute('SELECT * FROM plans WHERE teacher_id=? AND period=? AND type_id=?', (teacher_id, period, type_id)).fetchone()
            if data.get('id'):
                require(old is not None and old['id'] == integer(data['id']), 'Ключи существующего плана менять нельзя; создайте отдельную строку', 409)
            if old:
                require(old['version'] == integer(data.get('version')), 'План изменён; обновите список', 409)
                db.execute('UPDATE plans SET amount=?,note=?,version=version+1 WHERE id=?', (amount, note, old['id']))
                ident = old['id']
            else:
                ident = db.execute('INSERT INTO plans(teacher_id,period,type_id,amount,note) VALUES(?,?,?,?,?)', (teacher_id, period, type_id, amount, note)).lastrowid
            self.log(db, user, 'save', 'plans', ident, data)
        return {'id': ident}

    def report(self, user, period):
        period = month(period)
        entries = self.entries(user, {'period': period})
        plans = self.plans(user, period)
        settings = self.settings(user)
        academic = int(settings['academic_minutes'])
        result = {}
        for plan in plans:
            key = (plan['teacher_id'], plan['type_id'])
            result[key] = {'teacher_id': key[0], 'type_id': key[1], 'teacher': plan['teacher'], 'worktype': plan['worktype'], 'unit': plan['unit'], 'plan': plan['amount'], 'approved_minutes': 0, 'submitted_minutes': 0, 'draft_minutes': 0, 'returned_minutes': 0}
        for entry in entries:
            key = (entry['teacher_id'], entry['type_id'])
            if key not in result:
                result[key] = {'teacher_id': key[0], 'type_id': key[1], 'teacher': entry['teacher'], 'worktype': entry['worktype'], 'unit': entry['unit'], 'plan': '0', 'approved_minutes': 0, 'submitted_minutes': 0, 'draft_minutes': 0, 'returned_minutes': 0}
            result[key][entry['status'] + '_minutes'] += entry['minutes']
        for row in result.values():
            row['fact'] = units(row['approved_minutes'], row['unit'], academic)
            row['clock_hours'] = hours(row['approved_minutes'])
            row['deviation'] = str(Decimal(row['fact']) - Decimal(row['plan']))
            row['completion'] = str((Decimal(row['fact']) / Decimal(row['plan']) * 100).quantize(Decimal('.01'))) if Decimal(row['plan']) else None
        rows = sorted(result.values(), key=lambda r: (r['teacher'], r['worktype']))
        return {'period': period, 'settings': settings, 'rows': rows, 'entries': entries,
                'approved_minutes': sum(r['approved_minutes'] for r in rows),
                'submitted_minutes': sum(r['submitted_minutes'] for r in rows),
                'generated_at': now()}

    def dashboard(self, user, period):
        report = self.report(user, period)
        counts = {s: 0 for s in ('draft', 'submitted', 'approved', 'returned')}
        daily = {}
        for row in report['entries']:
            counts[row['status']] += 1
            if row['status'] == 'approved':
                daily[row['workday']] = daily.get(row['workday'], 0) + row['minutes']
        with self.connect() as db:
            closed = db.execute('SELECT closed FROM periods WHERE period=?', (period,)).fetchone()
        return {'period': period, 'counts': counts, 'daily': daily, 'approved_hours': hours(report['approved_minutes']), 'submitted_hours': hours(report['submitted_minutes']), 'closed': bool(closed and closed['closed'])}

    def period_set(self, user, data):
        self.admin(user)
        period = month(data.get('period'))
        closed = integer(data.get('closed'), 'Состояние', 0, 1)
        reason = text(data.get('reason'), 'Основание', 500)
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if closed:
                pending = db.execute("SELECT count(*) FROM entries WHERE substr(workday,1,7)=? AND status<>'approved'", (period,)).fetchone()[0]
                require(not pending, 'Остались несогласованные записи; завершите обработку', 409)
            db.execute('INSERT INTO periods(period,closed,actor_id,reason,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(period) DO UPDATE SET closed=excluded.closed,actor_id=excluded.actor_id,reason=excluded.reason,updated_at=excluded.updated_at', (period, closed, user['id'], reason, now()))
            self.log(db, user, 'period', 'periods', period, data)
        return {'closed': bool(closed)}

    def user_list(self, user):
        self.admin(user)
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT u.id,u.login,u.role,u.teacher_id,u.department_id,u.active,t.name teacher,d.name department FROM users u LEFT JOIN teachers t ON t.id=u.teacher_id LEFT JOIN departments d ON d.id=u.department_id ORDER BY u.login')]

    def user_save(self, user, data):
        self.admin(user)
        role = data.get('role')
        require(role in ROLES, 'Неизвестная роль')
        login = text(data.get('login'), 'Логин', 80)
        require(' ' not in login, 'Логин не должен содержать пробелы')
        active = integer(data.get('active', 1), 'Активность', 0, 1)
        teacher_id = integer(data['teacher_id']) if data.get('teacher_id') else None
        department_id = integer(data['department_id']) if data.get('department_id') else None
        require(role != 'teacher' or teacher_id is not None, 'Преподавателю нужен профиль')
        require(role != 'head' or department_id is not None, 'Руководителю нужна кафедра')
        ident = integer(data['id']) if data.get('id') else None
        if ident == user['id']:
            require(role == 'admin' and active, 'Нельзя отключить себя или понизить свою роль')
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if teacher_id:
                teacher = db.execute('SELECT * FROM teachers WHERE id=?', (teacher_id,)).fetchone()
                require(teacher, 'Профиль преподавателя не найден')
                require(not db.execute('SELECT id FROM users WHERE teacher_id=? AND id<>?', (teacher_id, ident or -1)).fetchone(), 'Профиль уже связан с другой учётной записью')
                require(role != 'head' or teacher['department_id'] == department_id, 'Кафедра руководителя и его профиль должны совпадать')
            if ident:
                require(db.execute('SELECT id FROM users WHERE id=?', (ident,)).fetchone(), 'Учётная запись не найдена', 404)
                db.execute('UPDATE users SET login=?,role=?,teacher_id=?,department_id=?,active=? WHERE id=?', (login, role, teacher_id, department_id, active, ident))
                if data.get('password'):
                    db.execute('UPDATE users SET password=? WHERE id=?', (password_hash(data['password']), ident))
            else:
                digest = password_hash(data.get('password'))
                ident = db.execute('INSERT INTO users(login,password,role,teacher_id,department_id,active) VALUES(?,?,?,?,?,?)', (login, digest, role, teacher_id, department_id, active)).lastrowid
            self.log(db, user, 'save', 'users', ident, {'login': login, 'role': role, 'active': active})
        return {'id': ident}

    def password_change(self, user, old, new):
        with self.connect() as db:
            row = db.execute('SELECT password FROM users WHERE id=?', (user['id'],)).fetchone()
            require(password_matches(str(old), row['password']), 'Текущий пароль неверен', 403)
            db.execute('UPDATE users SET password=? WHERE id=?', (password_hash(new), user['id']))
            self.log(db, user, 'password', 'users', user['id'], 'Пароль изменён')
        return {'changed': True}

    def audit(self, user, limit=300):
        self.admin(user)
        limit = integer(limit, 'Лимит', 1, 2000)
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT a.*,u.login actor FROM audit a LEFT JOIN users u ON u.id=a.actor_id ORDER BY a.id DESC LIMIT ?', (limit,))]

    def feedback(self, user):
        with self.connect() as db:
            sql = 'SELECT f.*,u.login actor FROM feedback f JOIN users u ON u.id=f.user_id'
            args = []
            if user['role'] != 'admin':
                sql += ' WHERE f.user_id=?'
                args.append(user['id'])
            return [dict(r) for r in db.execute(sql + ' ORDER BY f.id DESC', args)]

    def feedback_save(self, user, data):
        with self.connect() as db:
            if data.get('id'):
                self.admin(user)
                ident = integer(data['id'])
                answer = text(data.get('answer'), 'Ответ', 2000)
                require(db.execute('SELECT id FROM feedback WHERE id=?', (ident,)).fetchone(), 'Обращение не найдено', 404)
                db.execute('UPDATE feedback SET answer=?,answered_at=? WHERE id=?', (answer, now(), ident))
            else:
                subject = text(data.get('subject'), 'Тема', 120)
                message = text(data.get('message'), 'Сообщение', 2000)
                ident = db.execute('INSERT INTO feedback(user_id,subject,message,created_at) VALUES(?,?,?,?)', (user['id'], subject, message, now())).lastrowid
            self.log(db, user, 'feedback', 'feedback', ident, 'Обращение создано или обработано')
        return {'id': ident}

    def import_entries(self, user, rows, dry_run=True):
        require(isinstance(rows, list) and 0 < len(rows) <= 1000, 'Импорт должен содержать от 1 до 1000 строк')
        require(isinstance(dry_run, bool), 'Признак проверки должен быть логическим значением')
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            created = []
            try:
                for number, row in enumerate(rows, 2):
                    try:
                        require(isinstance(row, dict), 'Некорректная структура строки')
                        require(not row.get('id'), 'Импорт поддерживает только новые записи')
                        values = self.prepare_entry(db, user, row)
                        names = ','.join(values)
                        places = ','.join('?' for _ in values)
                        ident = db.execute(f'INSERT INTO entries({names},created_by,updated_at) VALUES({places},?,?)', [*values.values(), user['id'], now()]).lastrowid
                        created.append(ident)
                    except DomainError as error:
                        raise DomainError(f'Строка {number}: {error}', error.code)
                if dry_run:
                    db.rollback()
                else:
                    self.log(db, user, 'import', 'entries', '', {'count': len(created), 'ids': created})
            except Exception:
                db.rollback()
                raise
        return {'count': len(created), 'dry_run': bool(dry_run)}

    def backup(self, user):
        self.admin(user)
        folder = self.path.parent / 'backups'
        folder.mkdir(exist_ok=True)
        target = folder / ('Учёт-' + now().replace(':', '-').replace('+', '-') + '.sqlite')
        with self.lock, self.connect() as source:
            destination = sqlite3.connect(target)
            try:
                source.backup(destination)
                check = destination.execute('PRAGMA integrity_check').fetchone()[0]
                require(check == 'ok', 'Не удалось проверить резервную копию', 500)
            finally:
                destination.close()
        with self.connect() as db:
            self.log(db, user, 'backup', 'database', target.name, 'Резервная копия создана на сервере')
        return {'filename': target.name, 'integrity': 'ok'}

    def demo(self):
        with self.connect() as db:
            require(db.execute('SELECT count(*) FROM users').fetchone()[0] == 0, 'Демонстрационные данные можно создать только в пустой базе')
            db.executemany('INSERT INTO departments(name) VALUES(?)', [('Учебная кафедра А — модель',), ('Учебная кафедра Б — модель',)])
            for number in range(1, 11):
                db.execute('INSERT INTO teachers(name,department_id,position,rate) VALUES(?,?,?,?)',
                           (f'Преподаватель {number:02d} — тест', 1 if number < 7 else 2, 'Доцент — тестовая должность', '1'))
            kinds = [('Лекция', 'academic'), ('Практическое занятие', 'academic'), ('Консультация', 'clock'), ('Подготовка материалов', 'clock'), ('Проверка работ', 'clock'), ('Методическая работа', 'clock'), ('Научная работа', 'clock'), ('Организационная работа', 'clock'), ('Повышение квалификации', 'clock'), ('Воспитательная работа', 'clock')]
            db.executemany('INSERT INTO worktypes(name,unit) VALUES(?,?)', kinds)
            digest = password_hash('DemoTime2026!')
            admin_digest = password_hash('12345', demo=True)
            users = [('admin', admin_digest, 'admin', None, None), ('head', digest, 'head', None, 1), ('teacher', digest, 'teacher', 1, 1), ('teacher2', digest, 'teacher', 7, 2)]
            db.executemany('INSERT INTO users(login,password,role,teacher_id,department_id) VALUES(?,?,?,?,?)', users)
            period = date.today().strftime('%Y-%m')
            for teacher in range(1, 11):
                for kind in range(1, 11):
                    db.execute('INSERT INTO plans(teacher_id,period,type_id,amount,note) VALUES(?,?,?,?,?)', (teacher, period, kind, '20' if kind < 3 else '10', 'Проектный план; не документ МУИВ'))
            self.log(db, None, 'demo', 'database', '', 'Создана учебная база с вымышленными записями')
