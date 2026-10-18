"""Автоматическая проверка упакованного клиента на учебной базе."""
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sys
import time
import traceback
from tkinter import messagebox


def check_client(root, app, forms, destination):
    report = {'time_utc': datetime.now(timezone.utc).isoformat(),
              'platform': platform.platform(), 'python': platform.python_version(),
              'frozen': bool(getattr(sys, 'frozen', False)),
              'executable': sys.executable, 'forms': [], 'errors': []}
    original_error = messagebox.showerror
    messagebox.showerror = lambda title, message, **kwargs: report['errors'].append(str(message))
    root.report_callback_exception = lambda kind, value, tb: report['errors'].append(''.join(traceback.format_exception(kind, value, tb)))

    def wait():
        deadline = time.monotonic() + 15
        while app.busy and time.monotonic() < deadline:
            root.update()
            time.sleep(.01)
        root.update()
        if app.busy:
            raise RuntimeError('Истекло время ожидания запроса')
        if report['errors']:
            raise RuntimeError(report['errors'][-1])

    try:
        root.update()
        report['login_window'] = {'title': root.title(), 'mapped': bool(root.winfo_ismapped()),
                                  'width': root.winfo_width(), 'height': root.winfo_height()}
        if not root.winfo_ismapped():
            raise RuntimeError('Окно клиента не отображается')
        report['tcl'] = root.tk.call('info', 'patchlevel')
        report['tk'] = root.tk.call('package', 'require', 'Tk')
        app.login_var.set('admin')
        app.password_var.set('12345')
        app.login()
        wait()
        if app.api is None or app.api.user['role'] != 'admin':
            raise RuntimeError('Вход администратора не выполнен')
        report['login'] = 'admin: ok'
        for cls in forms:
            app.open(cls)
            wait()
            window = app.windows[cls.__name__]
            if not window.winfo_exists() or not window.winfo_ismapped():
                raise RuntimeError('Не отображается форма: ' + cls.__name__)
            report['forms'].append({'class': cls.__name__, 'title': window.title(),
                                    'width': window.winfo_width(), 'height': window.winfo_height(),
                                    'mapped': True})
            window.withdraw()
        app.api.logout()
        report['status'] = 'passed'
    except Exception:
        report['status'] = 'failed'
        report['errors'].append(traceback.format_exc())
    finally:
        messagebox.showerror = original_error
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        Path(destination).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        root.destroy()
    return 0 if report['status'] == 'passed' else 1
