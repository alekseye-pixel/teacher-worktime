"""Настольный клиент. Оконные формы Tkinter; сервер не доверяет данным клиента."""
from __future__ import annotations
import argparse
from datetime import date
import json
from pathlib import Path
import queue
import threading
from tk_support import configure_tk
configure_tk()
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import webbrowser

from api import Api, ApiError
from domain import ROLES, STATUSES, hours
import exports
import quality


UNIT_LABELS = {'clock': 'Астрономический час', 'academic': 'Академический час'}


class App:
    def __init__(self, root, server):
        self.root = root
        self.server = server
        self.api = None
        self.cache = {}
        self.settings = {}
        self.windows = {}
        self.events = queue.Queue()
        self.busy = 0
        self.period = tk.StringVar(value=date.today().strftime('%Y-%m'))
        self.status = tk.StringVar(value='Вход в систему')
        self.root.title('Учёт рабочего времени преподавателя')
        self.root.geometry('1050x720')
        self.root.minsize(1000, 680)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.style()
        self.main = ttk.Frame(root, padding=18)
        self.main.pack(fill='both', expand=True)
        ttk.Label(root, textvariable=self.status, padding=(14, 8), relief='sunken').pack(fill='x', side='bottom')
        self.root.after(60, self.dispatch)
        self.login_window()

    def style(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', font=('Arial', 11), foreground='#151515')
        style.configure('TButton', padding=(12, 8))
        style.configure('Treeview', rowheight=29)
        style.configure('Treeview.Heading', font=('Arial', 10, 'bold'), background='#e4e4e4')
        style.map('Treeview', background=[('selected', '#454545')], foreground=[('selected', '#ffffff')])
        style.configure('Title.TLabel', font=('Arial', 20, 'bold'))
        style.configure('Sub.TLabel', font=('Arial', 13, 'bold'))
        style.configure('Metric.TLabel', font=('Arial', 26, 'bold'))

    def dispatch(self):
        try:
            while True:
                callback, result, error = self.events.get_nowait()
                self.busy = max(0, self.busy - 1)
                if error:
                    self.status.set('Ошибка операции')
                    messagebox.showerror('Операция не выполнена', str(error), parent=self.root)
                else:
                    try:
                        callback(result)
                        self.status.set('Готово' if not self.busy else 'Выполняется запрос…')
                    except tk.TclError:
                        pass
        except queue.Empty:
            pass
        self.root.after(60, self.dispatch)

    def run(self, function, callback=lambda result: None):
        self.busy += 1
        self.status.set('Выполняется запрос…')
        def worker():
            try:
                result = function()
                self.events.put((callback, result, None))
            except Exception as error:
                self.events.put((callback, None, error))
        threading.Thread(target=worker, daemon=True).start()

    def login_window(self):
        for child in self.main.winfo_children():
            child.destroy()
        form = ttk.Frame(self.main, padding=28)
        form.place(relx=.5, rely=.43, anchor='center')
        ttk.Label(form, text='Учёт рабочего времени', style='Title.TLabel').grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 12))
        ttk.Label(form, text='Вход в настольный клиент').grid(row=1, column=0, columnspan=2, sticky='w', pady=(0, 20))
        self.server_var = tk.StringVar(value=self.server)
        self.login_var = tk.StringVar()
        self.password_var = tk.StringVar()
        fields = [('Сервер', self.server_var, ''), ('Логин', self.login_var, ''), ('Пароль', self.password_var, '*')]
        for index, (label, variable, mask) in enumerate(fields, 2):
            ttk.Label(form, text=label).grid(row=index, column=0, sticky='w', pady=8)
            entry = ttk.Entry(form, textvariable=variable, width=38, show=mask)
            entry.grid(row=index, column=1, sticky='ew', padx=(12, 0), pady=8)
            entry.bind('<Return>', lambda event: self.login())
        ttk.Button(form, text='Войти', command=self.login).grid(row=5, column=1, sticky='e', pady=18)
        ttk.Label(form, text='Учебная база содержит вымышленные записи.\nПароль не сохраняется на компьютере.', justify='left').grid(row=6, column=0, columnspan=2, sticky='w')

    def login(self):
        login = self.login_var.get()
        password = self.password_var.get()
        address = self.server_var.get()
        def action():
            api = Api(address)
            api.login(login, password)
            return api
        def success(api):
            self.api = api
            self.password_var.set('')
            self.server = address
            self.preload(self.home)
        self.run(action, success)

    def preload(self, callback):
        def action():
            catalogs = {name: self.api.call('catalogs', {'catalog': name}) for name in ('departments', 'teachers', 'worktypes')}
            settings = self.api.call('settings')
            return catalogs, settings
        def success(result):
            self.cache, self.settings = result
            callback()
        self.run(action, success)

    def home(self):
        for child in self.main.winfo_children():
            child.destroy()
        user = self.api.user
        ttk.Label(self.main, text='Рабочее место', style='Title.TLabel').pack(anchor='w')
        ttk.Label(self.main, text=f"{user['login']} · {ROLES[user['role']]} · {self.settings.get('organization', '')}").pack(anchor='w', pady=(8, 16))
        line = ttk.Frame(self.main)
        line.pack(fill='x', pady=(0, 16))
        ttk.Label(line, text='Период ГГГГ-ММ').pack(side='left')
        ttk.Entry(line, textvariable=self.period, width=12).pack(side='left', padx=10)
        ttk.Button(line, text='Обновить справочники', command=lambda: self.preload(lambda: None)).pack(side='left', padx=10)
        ttk.Button(line, text='Выйти', command=self.logout).pack(side='right')
        menu = ttk.Frame(self.main)
        menu.pack(fill='x')
        items = [
            ('Показатели', Dashboard, 'all'),
            ('Календарь учёта', Calendar, 'all'),
            ('Поиск записей', Search, 'all'),
            ('Контроль данных', Quality, 'all'),
            ('Личный кабинет', Profile, 'all'),
            ('Учёт времени', Entries, 'all'),
            ('План нагрузки', Plans, 'all'),
            ('Согласование', Review, 'manager'),
            ('Отчёт за месяц', Report, 'all'),
            ('Экспорт документов', Export, 'all'),
            ('Импорт записей', Import, 'all'),
            ('Преподаватели', Teachers, 'all'),
            ('Кафедры', Departments, 'all'),
            ('Виды работ', Worktypes, 'all'),
            ('Обращения', Feedback, 'all'),
            ('Пользователи', Users, 'admin'),
            ('Журнал событий', Audit, 'admin'),
            ('Настройки', Settings, 'admin'),
            ('Справка по системе', Help, 'all'),
        ]
        index = 0
        for label, form, permission in items:
            allowed = permission == 'all' or permission == 'admin' and user['role'] == 'admin' or permission == 'manager' and user['role'] in {'admin', 'head'}
            if not allowed:
                continue
            ttk.Button(menu, text=label, command=lambda cls=form: self.open(cls)).grid(row=index // 4, column=index % 4, sticky='ew', padx=5, pady=5)
            index += 1
        for column in range(4):
            menu.columnconfigure(column, weight=1)
        ttk.Label(self.main, text='Порядок работы', style='Sub.TLabel').pack(anchor='w', pady=(22, 8))
        instructions = '1. Укажите период и проверьте план.\n2. Добавьте фактические интервалы и передайте записи на проверку.\n3. Руководитель согласует запись или возвращает её с замечанием.\n4. В итоговый факт попадают только согласованные записи.'
        ttk.Label(self.main, text=instructions, justify='left', wraplength=920).pack(anchor='w')
        ttk.Label(self.main, text=self.settings.get('report_note', ''), wraplength=920).pack(anchor='w', pady=(20, 0))

    def open(self, cls):
        key = cls.__name__
        window = self.windows.get(key)
        if window and window.winfo_exists():
            window.deiconify()
            window.lift()
            return
        self.windows[key] = cls(self)

    def logout(self):
        for window in self.windows.values():
            if window.winfo_exists():
                window.destroy()
        self.windows.clear()
        if self.api:
            self.run(self.api.logout)
        self.api = None
        self.login_window()

    def close(self):
        if self.busy and not messagebox.askyesno('Завершение работы', 'Ещё выполняется запрос. Закрыть клиент?', parent=self.root):
            return
        self.root.destroy()

    def teacher_choices(self):
        return {str(row['id']): row['name'] for row in self.cache.get('teachers', []) if row['active']}

    def kind_choices(self):
        return {str(row['id']): row['name'] for row in self.cache.get('worktypes', []) if row['active']}

    def department_choices(self):
        return {str(row['id']): row['name'] for row in self.cache.get('departments', []) if row['active']}

    def default_teacher(self):
        return str(self.api.user['teacher_id'] or '')


class Window(tk.Toplevel):
    caption = 'Форма'
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title(self.caption)
        self.geometry('1120x690')
        self.minsize(820, 520)
        self.body = ttk.Frame(self, padding=16)
        self.body.pack(fill='both', expand=True)
        ttk.Label(self.body, text=self.caption, style='Title.TLabel').pack(anchor='w', pady=(0, 14))
        self.tools = ttk.Frame(self.body)
        self.tools.pack(fill='x', pady=(0, 12))
        self.note = tk.StringVar(value='')
        ttk.Label(self.body, textvariable=self.note, wraplength=1050).pack(anchor='w', side='bottom', pady=(12, 0))

    def button(self, title, callback):
        button = ttk.Button(self.tools, text=title, command=callback)
        button.pack(side='left', padx=(0, 8))
        return button

    def call(self, method, data, done):
        self.app.run(lambda: self.app.api.call(method, data), done)

    def refresh(self):
        pass

    def changed(self, result=None):
        self.refresh()


class GridWindow(Window):
    columns = ()
    def __init__(self, app):
        super().__init__(app)
        frame = ttk.Frame(self.body)
        frame.pack(fill='both', expand=True)
        self.gridview = ttk.Treeview(frame, columns=[c[0] for c in self.columns], show='headings', selectmode='browse')
        vertical = ttk.Scrollbar(frame, orient='vertical', command=self.gridview.yview)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=self.gridview.xview)
        self.gridview.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.gridview.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        for key, label, width in self.columns:
            self.gridview.heading(key, text=label)
            self.gridview.column(key, width=width, minwidth=70, stretch=key not in {'id', 'minutes', 'version'})
        self.rows = {}
        self.gridview.bind('<Double-1>', lambda event: self.edit_selected())

    def fill(self, rows):
        for item in self.gridview.get_children():
            self.gridview.delete(item)
        self.rows = {}
        for index, row in enumerate(rows):
            ident = str(index)
            self.rows[ident] = row
            values = []
            for key, label, width in self.columns:
                value = row.get(key, '')
                if key == 'status':
                    value = STATUSES.get(value, value)
                if key == 'unit':
                    value = UNIT_LABELS.get(value, value)
                if key == 'role':
                    value = ROLES.get(value, value)
                if key == 'active':
                    value = 'Да' if value else 'Нет'
                values.append(value if value is not None else '')
            self.gridview.insert('', 'end', iid=ident, values=values)
        self.note.set(f'Записей: {len(rows)}')

    def selected(self):
        items = self.gridview.selection()
        if not items:
            messagebox.showinfo('Выбор записи', 'Выберите строку в таблице', parent=self)
            return None
        return self.rows[items[0]]

    def edit_selected(self):
        pass


class Editor(tk.Toplevel):
    def __init__(self, parent, title, fields, record, on_save):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.resizable(True, True)
        self.record = dict(record)
        self.inputs = {}
        self.maps = {}
        frame = ttk.Frame(self, padding=20)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(1, weight=1)
        for index, field in enumerate(fields):
            key, label, kind, choices = field
            ttk.Label(frame, text=label, wraplength=200).grid(row=index, column=0, sticky='nw', pady=7, padx=(0, 18))
            variable = tk.StringVar(value=str(record.get(key, '') if record.get(key) is not None else ''))
            if kind == 'select':
                mapping = {str(k): str(v) for k, v in choices.items()}
                labels = {k: f'{k} — {v}' for k, v in mapping.items()}
                widget = ttk.Combobox(frame, values=list(labels.values()), state='readonly', width=52, textvariable=variable)
                variable.set(labels.get(str(record.get(key, '')), ''))
                self.maps[key] = {v: k for k, v in labels.items()}
            elif kind == 'text':
                widget = tk.Text(frame, width=58, height=4, wrap='word', font=('Arial', 11))
                widget.insert('1.0', variable.get())
                variable = widget
            elif kind == 'password':
                widget = ttk.Entry(frame, width=54, textvariable=variable, show='*')
            else:
                widget = ttk.Entry(frame, width=54, textvariable=variable)
            widget.grid(row=index, column=1, sticky='ew', pady=7)
            self.inputs[key] = variable
        buttons = ttk.Frame(frame)
        buttons.grid(row=len(fields), column=0, columnspan=2, sticky='e', pady=(16, 0))
        def save():
            data = dict(self.record)
            for key, value in self.inputs.items():
                result = value.get('1.0', 'end-1c') if isinstance(value, tk.Text) else value.get()
                if key in self.maps:
                    result = self.maps[key].get(result, '')
                data[key] = result
            on_save(data, self)
        ttk.Button(buttons, text='Отмена', command=self.destroy).pack(side='right', padx=5)
        ttk.Button(buttons, text='Сохранить', command=save).pack(side='right', padx=5)
        self.grab_set()


def field(key, label, kind='entry', choices=None):
    return key, label, kind, choices or {}


def save_dialog(window, dialog, method, data, callback=None):
    def success(result):
        dialog.destroy()
        if callback:
            callback(result)
        else:
            window.refresh()
    window.call(method, data, success)


class Dashboard(Window):
    caption = 'Показатели за месяц'
    def __init__(self, app):
        super().__init__(app)
        self.button('Обновить', self.refresh)
        self.metrics = ttk.Frame(self.body)
        self.metrics.pack(fill='x', pady=12)
        self.canvas = tk.Canvas(self.body, background='white', highlightthickness=1, highlightbackground='#d0d0d0', height=320)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda event: self.draw())
        self.result = None
        self.refresh()

    def refresh(self):
        self.call('dashboard', {'period': self.app.period.get()}, self.show)

    def show(self, result):
        self.result = result
        for child in self.metrics.winfo_children():
            child.destroy()
        values = [('Согласовано, ч', result['approved_hours']), ('На проверке, ч', result['submitted_hours']), ('Черновики', result['counts']['draft']), ('Возвращено', result['counts']['returned'])]
        for column, (label, value) in enumerate(values):
            frame = ttk.Frame(self.metrics, padding=12)
            frame.grid(row=0, column=column, sticky='ew')
            self.metrics.columnconfigure(column, weight=1)
            ttk.Label(frame, text=label).pack(anchor='w')
            ttk.Label(frame, text=str(value), style='Metric.TLabel').pack(anchor='w')
        self.note.set('Период закрыт' if result['closed'] else 'Период открыт. На графике только согласованное время в астрономических часах.')
        self.draw()

    def draw(self):
        if self.result is None:
            return
        self.canvas.delete('all')
        data = sorted(self.result['daily'].items())
        width = max(self.canvas.winfo_width(), 600)
        height = max(self.canvas.winfo_height(), 260)
        if not data:
            self.canvas.create_text(width / 2, height / 2, text='За период нет согласованных записей', font=('Arial', 13))
            return
        top = max(value for _, value in data)
        step = (width - 90) / len(data)
        self.canvas.create_line(55, 25, 55, height - 45, width - 20, height - 45)
        for index, (day, minutes) in enumerate(data):
            x = 65 + index * step
            bar_height = (height - 95) * minutes / max(top, 1)
            self.canvas.create_rectangle(x, height - 45 - bar_height, x + step * .65, height - 45, fill='#606060', outline='')
            self.canvas.create_text(x + step * .32, height - 29, text=day[-2:], font=('Arial', 9))
            self.canvas.create_text(x + step * .32, height - 56 - bar_height, text=hours(minutes), font=('Arial', 9))


class Profile(Window):
    caption = 'Личный кабинет'
    def __init__(self, app):
        super().__init__(app)
        self.button('Изменить пароль', self.password)
        user = app.api.user
        teacher = next((row for row in app.cache.get('teachers', []) if row['id'] == user['teacher_id']), None)
        values = [('Логин', user['login']), ('Роль', ROLES[user['role']]), ('Профиль преподавателя', teacher['name'] if teacher else 'Не связан'), ('Сервер', app.server), ('Сеанс', 'Действует один час с момента входа')]
        for label, value in values:
            line = ttk.Frame(self.body, padding=(0, 10))
            line.pack(fill='x')
            ttk.Label(line, text=label, width=26).pack(side='left')
            ttk.Label(line, text=str(value)).pack(side='left')
        self.note.set('После смены пароля все сеансы этой учётной записи завершаются. Войдите заново.')

    def password(self):
        fields = [field('old', 'Текущий пароль', 'password'), field('new', 'Новый пароль', 'password'), field('repeat', 'Повтор нового пароля', 'password')]
        def save(data, dialog):
            if data['new'] != data['repeat']:
                messagebox.showerror('Пароль', 'Новые пароли не совпадают', parent=dialog)
                return
            def success(result):
                dialog.destroy()
                self.app.logout()
            self.call('password', data, success)
        Editor(self, 'Смена пароля', fields, {}, save)


class Catalog(GridWindow):
    catalog = ''
    def __init__(self, app):
        super().__init__(app)
        self.button('Обновить', self.refresh)
        if app.api.user['role'] == 'admin':
            self.button('Добавить', self.add)
            self.button('Изменить', self.edit_selected)
        self.refresh()

    def refresh(self):
        self.call('catalogs', {'catalog': self.catalog}, self.fill)

    def fields(self):
        return [field('name', 'Наименование'), field('active', 'Действует', 'select', {'1': 'Да', '0': 'Нет'})]

    def add(self):
        self.editor({'active': 1})

    def edit_selected(self):
        if self.app.api.user['role'] != 'admin':
            return
        row = self.selected()
        if row:
            self.editor(row)

    def editor(self, record):
        def save(data, dialog):
            payload = {'catalog': self.catalog, 'record': data}
            def success(result):
                dialog.destroy()
                self.app.preload(self.refresh)
            self.call('catalog.save', payload, success)
        Editor(self, self.caption, self.fields(), record, save)


class Departments(Catalog):
    caption = 'Кафедры'
    catalog = 'departments'
    columns = [('id', 'Код', 70), ('name', 'Наименование', 500), ('active', 'Действует', 110)]


class Teachers(Catalog):
    caption = 'Преподаватели'
    catalog = 'teachers'
    columns = [('id', 'Код', 70), ('name', 'Преподаватель', 300), ('department', 'Кафедра', 250), ('position', 'Должность', 180), ('rate', 'Ставка', 90), ('active', 'Действует', 100)]
    def fields(self):
        return [field('name', 'ФИО или тестовое имя'), field('department_id', 'Кафедра', 'select', self.app.department_choices()), field('position', 'Должность'), field('rate', 'Доля ставки'), field('active', 'Действует', 'select', {'1': 'Да', '0': 'Нет'})]

    def add(self):
        self.editor({'active': 1, 'rate': '1'})


class Worktypes(Catalog):
    caption = 'Виды работ'
    catalog = 'worktypes'
    columns = [('id', 'Код', 70), ('name', 'Вид работы', 450), ('unit', 'Единица плана', 230), ('active', 'Действует', 110)]
    def fields(self):
        return [field('name', 'Наименование'), field('unit', 'Единица плана', 'select', UNIT_LABELS), field('active', 'Действует', 'select', {'1': 'Да', '0': 'Нет'})]

    def add(self):
        self.editor({'active': 1, 'unit': 'clock'})


class Entries(GridWindow):
    caption = 'Учёт фактического времени'
    columns = [('id', '№', 65), ('workday', 'Дата', 115), ('start', 'Начало', 85), ('end', 'Окончание', 100), ('teacher', 'Преподаватель', 230), ('worktype', 'Вид работы', 190), ('minutes', 'Минуты', 85), ('status', 'Состояние', 140), ('title', 'Содержание', 300)]
    def __init__(self, app):
        super().__init__(app)
        self.button('Обновить', self.refresh)
        self.button('Добавить', self.add)
        self.button('Изменить', self.edit_selected)
        self.button('Удалить', self.delete)
        self.button('На проверку', lambda: self.change('submitted'))
        self.button('Отозвать', lambda: self.change('draft'))
        self.button('История', self.history)
        self.refresh()

    def refresh(self):
        self.call('entries', {'period': self.app.period.get()}, self.fill)

    def fields(self):
        return [field('teacher_id', 'Преподаватель', 'select', self.app.teacher_choices()), field('workday', 'Дата ГГГГ-ММ-ДД'), field('start', 'Начало ЧЧ:ММ'), field('end', 'Окончание ЧЧ:ММ'), field('type_id', 'Вид работы', 'select', self.app.kind_choices()), field('title', 'Содержание работы', 'text'), field('evidence', 'Документ или основание', 'text')]

    def add(self):
        record = {'teacher_id': self.app.default_teacher(), 'workday': date.today().isoformat(), 'start': '09:00', 'end': '10:30'}
        self.editor(record)

    def edit_selected(self):
        row = self.selected()
        if row:
            self.editor(row)

    def editor(self, record):
        def save(data, dialog):
            save_dialog(self, dialog, 'entry.save', data)
        Editor(self, 'Запись о выполненной работе', self.fields(), record, save)

    def delete(self):
        row = self.selected()
        if row and messagebox.askyesno('Удаление черновика', f"Удалить черновик №{row['id']}?", parent=self):
            self.call('entry.delete', {'id': row['id'], 'version': row['version']}, self.changed)

    def change(self, target):
        row = self.selected()
        if row:
            self.call('entry.transition', {'id': row['id'], 'version': row['version'], 'target': target}, self.changed)

    def history(self):
        row = self.selected()
        if row:
            self.call('entry.history', {'id': row['id']}, lambda rows: History(self.app, rows, row['id']))


class History(GridWindow):
    caption = 'История согласования записи'
    columns = [('created_at', 'Время', 220), ('actor', 'Пользователь', 150), ('previous', 'Было', 120), ('target', 'Стало', 120), ('reason', 'Комментарий', 450)]
    def __init__(self, app, rows, ident):
        super().__init__(app)
        display = []
        for row in rows:
            row = dict(row)
            row['previous'] = STATUSES.get(row['previous'], row['previous'])
            row['target'] = STATUSES.get(row['target'], row['target'])
            display.append(row)
        self.fill(display)
        self.note.set(f'Запись №{ident}. История решений не редактируется через приложение.')


class Plans(GridWindow):
    caption = 'План нагрузки на месяц'
    columns = [('id', 'Код', 70), ('teacher', 'Преподаватель', 300), ('worktype', 'Вид работы', 240), ('unit', 'Единица', 210), ('amount', 'План', 100), ('note', 'Примечание', 300)]
    def __init__(self, app):
        super().__init__(app)
        self.button('Обновить', self.refresh)
        if app.api.user['role'] in {'head', 'admin'}:
            self.button('Добавить', self.add)
            self.button('Изменить', self.edit_selected)
        self.refresh()

    def refresh(self):
        self.call('plans', {'period': self.app.period.get()}, self.fill)

    def fields(self):
        return [field('teacher_id', 'Преподаватель', 'select', self.app.teacher_choices()), field('period', 'Период ГГГГ-ММ'), field('type_id', 'Вид работы', 'select', self.app.kind_choices()), field('amount', 'План в единицах вида работ'), field('note', 'Примечание', 'text')]

    def add(self):
        self.editor({'period': self.app.period.get(), 'amount': '0'})

    def edit_selected(self):
        if self.app.api.user['role'] not in {'head', 'admin'}:
            return
        row = self.selected()
        if row:
            self.editor(row)

    def editor(self, record):
        def save(data, dialog):
            save_dialog(self, dialog, 'plan.save', data)
        Editor(self, 'Плановая нагрузка', self.fields(), record, save)


class Review(Entries):
    caption = 'Согласование рабочего времени'
    def __init__(self, app):
        GridWindow.__init__(self, app)
        self.button('Обновить', self.refresh)
        self.button('Согласовать', lambda: self.decision('approved'))
        self.button('Вернуть', lambda: self.decision('returned'))
        self.button('История', self.history)
        self.refresh()

    def refresh(self):
        self.call('entries', {'period': self.app.period.get(), 'status': 'submitted'}, self.fill)

    def edit_selected(self):
        self.history()

    def decision(self, target):
        row = self.selected()
        if not row:
            return
        fields = [field('reason', 'Комментарий к решению', 'text')]
        def save(data, dialog):
            payload = {'id': row['id'], 'version': row['version'], 'target': target, 'reason': data.get('reason', '')}
            save_dialog(self, dialog, 'entry.transition', payload)
        Editor(self, 'Решение по записи №' + str(row['id']), fields, {}, save)


class Report(GridWindow):
    caption = 'План и согласованный факт'
    columns = [('teacher', 'Преподаватель', 250), ('worktype', 'Вид работы', 220), ('unit', 'Единица', 200), ('plan', 'План', 90), ('fact', 'Факт', 90), ('deviation', 'Отклонение', 110), ('clock_hours', 'Астрономические ч', 150), ('submitted_minutes', 'На проверке, мин', 140)]
    def __init__(self, app):
        super().__init__(app)
        self.button('Обновить', self.refresh)
        self.button('Экспорт', lambda: app.open(Export))
        self.refresh()

    def refresh(self):
        self.call('report', {'period': self.app.period.get()}, self.show)

    def show(self, result):
        self.fill(result['rows'])
        self.note.set(f"Период {result['period']}. Согласовано {hours(result['approved_minutes'])} астрономических ч. Строки с разными единицами плана между собой не суммируются.")


class Export(Window):
    caption = 'Формирование документов'
    def __init__(self, app):
        super().__init__(app)
        self.button('DOCX', lambda: self.export('docx'))
        self.button('XLSX', lambda: self.export('xlsx'))
        self.button('CSV', lambda: self.export('csv'))
        ttk.Label(self.body, text='Документ содержит план, согласованный факт и отдельные показатели записей на проверке.\n\nВыберите формат и место сохранения на своём компьютере. Экспорт не изменяет состояние записей.', justify='left', wraplength=980).pack(anchor='w', pady=22)
        self.note.set('Для DOCX требуется python-docx; для XLSX — openpyxl. CSV работает без дополнительных библиотек.')

    def export(self, extension):
        path = filedialog.asksaveasfilename(parent=self, title='Сохранить отчёт', defaultextension='.' + extension, initialfile='Отчёт ' + self.app.period.get() + '.' + extension, filetypes=[(extension.upper(), '*.' + extension)])
        if not path:
            return
        method = {'docx': exports.to_docx, 'xlsx': exports.to_xlsx, 'csv': exports.to_csv}[extension]
        def action():
            result = self.app.api.call('report', {'period': self.app.period.get()})
            return str(method(result, path))
        self.app.run(action, lambda filename: messagebox.showinfo('Документ создан', filename, parent=self))


class Import(Window):
    caption = 'Импорт записей из CSV'
    def __init__(self, app):
        super().__init__(app)
        self.path = None
        self.rows = None
        self.button('Сохранить шаблон', self.template)
        self.button('Выбрать CSV', self.choose)
        self.button('Проверить', self.validate)
        self.button('Загрузить', self.commit)
        self.preview = tk.Text(self.body, wrap='none', font=('Consolas', 10), height=22)
        self.preview.pack(fill='both', expand=True)
        self.note.set('Разделитель — точка с запятой, кодировка UTF-8. Загрузка выполняется целиком или откатывается. Используйте коды из справочников.')

    def template(self):
        path = filedialog.asksaveasfilename(parent=self, defaultextension='.csv', initialfile='Шаблон импорта.csv', filetypes=[('CSV', '*.csv')])
        if path:
            exports.import_template(path)
            self.note.set('Шаблон сохранён: ' + path)

    def choose(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[('CSV', '*.csv')])
        if not path:
            return
        try:
            rows = exports.read_import(path)
        except (OSError, ValueError, UnicodeError) as error:
            messagebox.showerror('Файл не прочитан', str(error), parent=self)
            return
        self.path = path
        self.rows = rows
        self.preview.delete('1.0', 'end')
        for index, row in enumerate(rows[:30], 2):
            self.preview.insert('end', f"{index}: {row['workday']} {row['start']}–{row['end']} | {row['teacher_id']} | {row['title']}\n")
        self.note.set(f'Файл: {Path(path).name}. Всего строк: {len(rows)}. Показаны первые 30.')

    def validate(self):
        if self.rows:
            self.call('import', {'rows': self.rows, 'dry_run': True}, lambda result: messagebox.showinfo('Проверка завершена', f"Корректных строк: {result['count']}. База не изменена.", parent=self))
        else:
            messagebox.showinfo('Импорт', 'Сначала выберите файл CSV', parent=self)

    def commit(self):
        if not self.rows:
            messagebox.showinfo('Импорт', 'Сначала выберите файл CSV', parent=self)
            return
        if not messagebox.askyesno('Импорт', f'Добавить {len(self.rows)} записей как черновики?', parent=self):
            return
        def success(result):
            self.note.set(f"Загружено записей: {result['count']}")
            self.rows = None
        self.call('import', {'rows': self.rows, 'dry_run': False}, success)


class Users(GridWindow):
    caption = 'Учётные записи пользователей'
    columns = [('id', 'Код', 70), ('login', 'Логин', 150), ('role', 'Роль', 160), ('teacher', 'Профиль', 280), ('department', 'Кафедра', 240), ('active', 'Действует', 100)]
    def __init__(self, app):
        super().__init__(app)
        self.button('Обновить', self.refresh)
        self.button('Добавить', self.add)
        self.button('Изменить', self.edit_selected)
        self.refresh()

    def refresh(self):
        self.call('users', {}, self.fill)

    def fields(self):
        return [field('login', 'Логин'), field('role', 'Роль', 'select', ROLES), field('teacher_id', 'Профиль преподавателя', 'select', {'': 'Не связан', **self.app.teacher_choices()}), field('department_id', 'Кафедра руководителя', 'select', {'': 'Не указана', **self.app.department_choices()}), field('active', 'Действует', 'select', {'1': 'Да', '0': 'Нет'}), field('password', 'Новый пароль; пусто — без смены', 'password')]

    def add(self):
        self.editor({'active': 1, 'role': 'teacher'})

    def edit_selected(self):
        row = self.selected()
        if row:
            self.editor(row)

    def editor(self, row):
        def save(data, dialog):
            save_dialog(self, dialog, 'user.save', data)
        Editor(self, 'Учётная запись', self.fields(), row, save)


class Audit(GridWindow):
    caption = 'Журнал событий'
    columns = [('id', '№', 70), ('created_at', 'Время', 230), ('actor', 'Пользователь', 150), ('event', 'Операция', 150), ('entity', 'Объект', 120), ('entity_id', 'Код', 80), ('details', 'Изменения', 500)]
    def __init__(self, app):
        super().__init__(app)
        self.button('Обновить', self.refresh)
        self.button('Экспорт журнала', self.export)
        self.refresh()

    def refresh(self):
        self.call('audit', {'limit': 500}, self.fill)

    def edit_selected(self):
        row = self.selected()
        if row:
            Detail(self.app, 'Событие №' + str(row['id']), row['details'])

    def export(self):
        path = filedialog.asksaveasfilename(parent=self, defaultextension='.json', initialfile='Журнал событий.json', filetypes=[('JSON', '*.json')])
        if path:
            Path(path).write_text(json.dumps(list(self.rows.values()), ensure_ascii=False, indent=2), encoding='utf-8')
            self.note.set('Журнал сохранён на компьютере администратора: ' + path)


class Detail(Window):
    caption = 'Просмотр сведений'
    def __init__(self, app, title, content):
        super().__init__(app)
        self.title(title)
        area = tk.Text(self.body, wrap='word', font=('Arial', 11), padx=12, pady=12)
        area.pack(fill='both', expand=True)
        area.insert('1.0', content)
        area.configure(state='disabled')


class Settings(Window):
    caption = 'Настройки и обслуживание'
    def __init__(self, app):
        super().__init__(app)
        self.button('Параметры', self.parameters)
        self.button('Закрыть период', lambda: self.period_set(1))
        self.button('Открыть период', lambda: self.period_set(0))
        self.button('Резервная копия', self.backup)
        self.button('Сведения о сервере', self.about)
        self.values = tk.Text(self.body, wrap='word', font=('Arial', 11), padx=12, pady=12)
        self.values.pack(fill='both', expand=True)
        self.refresh()

    def refresh(self):
        self.call('settings', {}, self.show)

    def show(self, result):
        self.app.settings = result
        self.values.configure(state='normal')
        self.values.delete('1.0', 'end')
        labels = {'organization': 'Организация', 'academic_minutes': 'Академический час, мин', 'weekly_hours': 'Недельная продолжительность, ч', 'report_note': 'Примечание к отчёту', 'contact': 'Поддержка'}
        for key, label in labels.items():
            self.values.insert('end', f'{label}: {result.get(key, "")}\n\n')
        self.values.configure(state='disabled')

    def parameters(self):
        fields = [field('organization', 'Организация'), field('academic_minutes', 'Академический час, мин'), field('weekly_hours', 'Неделя, ч'), field('report_note', 'Примечание', 'text'), field('contact', 'Поддержка', 'text')]
        def save(data, dialog):
            save_dialog(self, dialog, 'settings.save', data)
        Editor(self, 'Параметры системы', fields, self.app.settings, save)

    def period_set(self, closed):
        fields = [field('period', 'Период ГГГГ-ММ'), field('reason', 'Основание решения', 'text')]
        def save(data, dialog):
            data['closed'] = closed
            save_dialog(self, dialog, 'period.set', data)
        Editor(self, 'Закрытие периода' if closed else 'Возобновление периода', fields, {'period': self.app.period.get()}, save)

    def backup(self):
        self.call('backup', {}, lambda result: messagebox.showinfo('Резервная копия', 'Создана на сервере: ' + result['filename'] + '\nПроверка целостности: ' + result['integrity'], parent=self))

    def about(self):
        Detail(self.app, 'Подключение к серверу', 'Адрес: ' + self.app.server + '\n\nФайлы базы и резервные копии находятся на сервере. Пользовательский клиент не получает прямого доступа к файлам СУБД. Для работы через сеть нужен HTTPS с доверенным сертификатом.')


class Feedback(GridWindow):
    caption = 'Обращения в поддержку'
    columns = [('id', '№', 70), ('created_at', 'Создано', 230), ('actor', 'Автор', 150), ('subject', 'Тема', 250), ('answer', 'Ответ', 400)]
    def __init__(self, app):
        super().__init__(app)
        self.button('Обновить', self.refresh)
        self.button('Новое обращение', self.add)
        self.button('Открыть', self.edit_selected)
        self.refresh()

    def refresh(self):
        self.call('feedback', {}, self.fill)

    def add(self):
        fields = [field('subject', 'Тема'), field('message', 'Сообщение', 'text')]
        def save(data, dialog):
            save_dialog(self, dialog, 'feedback.save', data)
        Editor(self, 'Новое обращение', fields, {}, save)

    def edit_selected(self):
        row = self.selected()
        if not row:
            return
        if self.app.api.user['role'] == 'admin':
            fields = [field('answer', 'Ответ', 'text')]
            def save(data, dialog):
                save_dialog(self, dialog, 'feedback.save', data)
            Editor(self, row['subject'] + '\n' + row['message'][:100], fields, row, save)
        else:
            Detail(self.app, row['subject'], row['message'] + '\n\nОтвет:\n' + (row['answer'] or 'Обращение ещё не обработано'))


class Help(Window):
    caption = 'Справка по системе'
    def __init__(self, app):
        super().__init__(app)
        self.button('Порядок учёта', lambda: self.section('workflow'))
        self.button('Единицы времени', lambda: self.section('units'))
        self.button('Роли и доступ', lambda: self.section('roles'))
        self.button('Ошибки и поддержка', lambda: self.section('errors'))
        self.button('Обслуживание', lambda: self.section('maintenance'))
        self.area = tk.Text(self.body, wrap='word', padx=16, pady=16, font=('Arial', 12), spacing3=8)
        self.area.pack(fill='both', expand=True)
        self.section('workflow')

    def section(self, name):
        content = {
            'workflow': 'ПОРЯДОК УЧЁТА\n\nВыберите период на главной форме. В форме «Учёт времени» создайте запись с датой, интервалом, видом и содержанием работы. Сначала запись сохраняется как черновик.\n\nПроверьте запись и передайте её на согласование. Руководитель видит записи своей кафедры. Согласованную запись редактировать нельзя. Возвращённая запись сохраняет комментарий руководителя; после исправления её можно направить повторно.\n\nЗакрытие периода выполняет администратор после согласования всех записей. Повторное открытие требует указания основания и фиксируется в журнале.',
            'units': 'ЕДИНИЦЫ ВРЕМЕНИ\n\nВ базе хранится длительность каждого интервала в целых минутах. Астрономические часы определяются делением на 60. Для учебных занятий количество академических часов определяется делением на длительность академического часа. В проектной базе это 45 минут.\n\nПлан и факт сопоставляются внутри одного вида работы в его единице. Нельзя складывать академические часы лекций и астрономические часы методической работы. Сводная продолжительность в астрономических часах рассчитывается по исходным минутам.\n\nПоказатели системы не заменяют юридически значимый табель и не используются для автоматического начисления зарплаты.',
            'roles': 'РОЛИ И ДОСТУП\n\nПреподаватель ведёт свои записи, просматривает свой план, отчёты и обращения.\n\nРуководитель просматривает преподавателей закреплённой кафедры, задаёт план и принимает решения по переданным записям. Согласовать собственную запись нельзя.\n\nАдминистратор управляет справочниками, пользователями, настройками и периодами, создаёт резервные копии. Проверки доступа действуют на сервере, а не только в меню клиента.',
            'errors': 'ОШИБКИ И ПОДДЕРЖКА\n\n«Нет соединения» — проверьте адрес и работу сервера.\n\n«Сеанс завершён» — войдите заново.\n\n«Пересечение» — у преподавателя уже есть запись в этом интервале. Соседние интервалы, например 09:00–10:00 и 10:00–11:00, разрешены.\n\n«Запись изменена» — обновите таблицу и повторите действие по актуальной версии.\n\n«Период закрыт» — обратитесь к администратору. Не изменяйте базу напрямую.\n\nФорма «Обращения» позволяет направить вопрос администратору системы.',
            'maintenance': 'ОБСЛУЖИВАНИЕ\n\nРезервную копию создаёт администратор. Она хранится в каталоге backups рядом с серверной базой; перед выдачей результата выполняется PRAGMA integrity_check.\n\nВосстановление выполняется только при остановленном сервере с помощью restore.py. Перед восстановлением сохраните текущую базу и проверьте выбранную копию.\n\nДля сетевого использования требуются HTTPS, доверенный сертификат, разграничение доступа к серверной папке и согласование обработки персональных данных. Демонстрационные пароли нельзя использовать в рабочей среде.',
        }
        self.area.configure(state='normal')
        self.area.delete('1.0', 'end')
        self.area.insert('1.0', content[name])
        self.area.configure(state='disabled')


def main():
    parser = argparse.ArgumentParser(description='Клиент учёта времени')
    parser.add_argument('--server', default='http://127.0.0.1:8765')
    parser.add_argument('--self-test', type=Path, metavar='REPORT.json', help='Проверка клиента на отдельном демонстрационном сервере')
    args = parser.parse_args()
    root = tk.Tk()
    app = App(root, args.server)
    if args.self_test:
        from startup_check import check_client
        forms = [Dashboard, Profile, Entries, Plans, Review, Report, Export, Import,
                 Teachers, Departments, Worktypes, Feedback, Users, Audit, Settings,
                 Help, Calendar, Quality, Search]
        raise SystemExit(check_client(root, app, forms, args.self_test))
    root.mainloop()




class Calendar(GridWindow):
    caption = 'Календарь учёта за месяц'
    columns = [('workday', 'Дата', 170), ('entries', 'Записей', 110), ('approved', 'Согласовано, ч', 170), ('submitted', 'На проверке, ч', 170), ('draft', 'Черновики, ч', 170), ('returned', 'Возвращено, ч', 170)]

    def __init__(self, app):
        super().__init__(app)
        self.button('Обновить', self.refresh)
        self.button('Показатели', lambda: app.open(Dashboard))
        self.refresh()

    def refresh(self):
        self.call('calendar', {'period': self.app.period.get()}, self.show)

    def show(self, rows):
        self.fill(rows)
        self.note.set('Все показатели выражены в астрономических часах по исходным минутам. Пустой день означает отсутствие записей, а не отсутствие работы.')


class Quality(GridWindow):
    caption = 'Контроль данных и незавершённых записей'
    columns = [('level', 'Уровень', 120), ('kind', 'Проверка', 200), ('entry_id', 'Запись', 80), ('teacher', 'Преподаватель', 230), ('workday', 'Дата', 130), ('message', 'Замечание', 550)]

    def __init__(self, app):
        super().__init__(app)
        self.button('Обновить', self.refresh)
        self.button('Экспорт замечаний', self.export)
        self.button('Открыть описание', self.edit_selected)
        self.refresh()

    def refresh(self):
        self.call('quality', {'period': self.app.period.get()}, self.show)

    def show(self, rows):
        self.fill(rows)
        errors = sum(row['level'] == 'Ошибка' for row in rows)
        warnings = sum(row['level'] == 'Внимание' for row in rows)
        self.note.set(f'Ошибок целостности: {errors}. Сигналов проверки: {warnings}. Эти сообщения не являются выводами о нарушении трудовой дисциплины.')

    def edit_selected(self):
        row = self.selected()
        if row:
            Detail(self.app, row['kind'], row['message'])

    def export(self):
        path = filedialog.asksaveasfilename(parent=self, initialfile='Контроль данных.csv', defaultextension='.csv', filetypes=[('CSV', '*.csv')])
        if path:
            quality.export_quality(list(self.rows.values()), path)
            self.note.set('Замечания сохранены: ' + path)


class Search(GridWindow):
    caption = 'Поиск записей за месяц'
    columns = Entries.columns

    def __init__(self, app):
        super().__init__(app)
        self.query = tk.StringVar()
        ttk.Label(self.tools, text='Текст').pack(side='left')
        ttk.Entry(self.tools, textvariable=self.query, width=28).pack(side='left', padx=8)
        self.status = tk.StringVar(value='Все состояния')
        self.state_map = {'Все состояния': '', **{label: key for key, label in STATUSES.items()}}
        ttk.Combobox(self.tools, values=list(self.state_map), textvariable=self.status, state='readonly', width=18).pack(side='left', padx=8)
        self.kind = tk.StringVar(value='Все виды работ')
        self.kind_map = {'Все виды работ': '', **{str(key) + ' — ' + value: key for key, value in self.app.kind_choices().items()}}
        ttk.Combobox(self.tools, values=list(self.kind_map), textvariable=self.kind, state='readonly', width=25).pack(side='left', padx=8)
        self.button('Найти', self.refresh)
        self.refresh()

    def refresh(self):
        data = {'period': self.app.period.get(), 'query': self.query.get(), 'status': self.state_map.get(self.status.get(), ''), 'type_id': self.kind_map.get(self.kind.get(), '')}
        self.call('search', data, self.fill)

    def edit_selected(self):
        row = self.selected()
        if row:
            content = '\n\n'.join([row['teacher'], row['workday'] + ' ' + row['start'] + '–' + row['end'], row['worktype'], row['title'], 'Основание: ' + (row['evidence'] or 'Не указано'), 'Состояние: ' + STATUSES[row['status']]])
            Detail(self.app, 'Запись №' + str(row['id']), content)


if __name__ == '__main__':
    main()
