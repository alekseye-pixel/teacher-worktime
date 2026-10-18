"""Пути Tcl/Tk для Windows, включая длинные пути и каталоги пользователя."""
import os
from pathlib import Path
import sys


def configure_tk():
    if os.name != 'nt':
        return
    bundled = getattr(sys, 'frozen', False)
    root = Path(sys._MEIPASS) if bundled else Path(sys.base_prefix) / 'tcl'
    for variable, folder in [('TCL_LIBRARY', '_tcl_data' if bundled else 'tcl8.6'),
                             ('TK_LIBRARY', '_tk_data' if bundled else 'tk8.6')]:
        path = Path(os.environ.get(variable) or root / folder)
        if path.is_dir():
            value = str(path.resolve())
            if not value.startswith('\\\\?\\'):
                value = '\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value
            os.environ[variable] = value.replace('\\', '/')
