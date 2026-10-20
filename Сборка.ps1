param(
    [string]$PythonExe,
    [string]$EnvironmentPath = ".venv",
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:NO_PROXY = (($env:NO_PROXY + ",localhost,127.0.0.1,::1").Trim(','))
Push-Location $PSScriptRoot
try {
    if (-not [IO.Path]::IsPathRooted($EnvironmentPath)) {
        $EnvironmentPath = Join-Path $PSScriptRoot $EnvironmentPath
    }
    $ProjectPython = Join-Path $EnvironmentPath "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $ProjectPython)) {
        if ($PythonExe) {
            & $PythonExe -m venv $EnvironmentPath
        } elseif (Get-Command py -ErrorAction SilentlyContinue) {
            py -3.12 -m venv $EnvironmentPath
        } elseif (Get-Command python -ErrorAction SilentlyContinue) {
            python -m venv $EnvironmentPath
        } else {
            throw "Установите Python 3.12 с Tcl/Tk или укажите -PythonExe ПУТЬ-К-python.exe."
        }
        if ($LASTEXITCODE -ne 0) { throw "Не удалось создать виртуальное окружение." }
    }
    & $ProjectPython -c "import sys; assert sys.version_info[:2] == (3, 12), 'Для сборки нужен Python 3.12'; print(sys.version)"
    if ($LASTEXITCODE -ne 0) { throw "Неподходящая версия Python." }
    $TkConfiguration = & $ProjectPython -c "from tk_support import configure_tk; configure_tk(); import os, json; print(json.dumps({key: os.environ.get(key, '') for key in ['TCL_LIBRARY', 'TK_LIBRARY']}))"
    if ($LASTEXITCODE -ne 0) { throw "Не удалось определить пути Tcl/Tk." }
    $TkPaths = $TkConfiguration | ConvertFrom-Json
    if ($TkPaths.TCL_LIBRARY) { $env:TCL_LIBRARY = $TkPaths.TCL_LIBRARY }
    if ($TkPaths.TK_LIBRARY) { $env:TK_LIBRARY = $TkPaths.TK_LIBRARY }
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $EnvironmentPath "pyinstaller-cache"
    $env:PIP_CACHE_DIR = Join-Path $EnvironmentPath "pip-cache"
    $BuildTemp = Join-Path $EnvironmentPath "temp"
    New-Item -ItemType Directory -Path $BuildTemp -Force | Out-Null
    $env:TEMP = $BuildTemp
    $env:TMP = $BuildTemp
    & $ProjectPython -c "import tkinter; root = tkinter.Tk(); print('Tcl/Tk:', root.tk.call('info', 'patchlevel')); root.destroy()"
    if ($LASTEXITCODE -ne 0) { throw "Проверка Tcl/Tk не пройдена. Нужна полная установка Python с компонентом Tcl/Tk." }
    if (-not $SkipInstall) {
        $Requirements = if (Test-Path -LiteralPath "requirements-build.lock.txt") { "requirements-build.lock.txt" } else { "requirements.txt" }
        & $ProjectPython -m pip install -r $Requirements
        if ($LASTEXITCODE -ne 0) { throw "Не удалось установить зависимости." }
        & $ProjectPython -m pip install "pyinstaller>=6,<7"
        if ($LASTEXITCODE -ne 0) { throw "Не удалось установить PyInstaller." }
    }
    & $ProjectPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw "Обнаружен конфликт зависимостей." }
    & $ProjectPython -m unittest -v test_system.py
    if ($LASTEXITCODE -ne 0) { throw "Интеграционные проверки не пройдены." }
    & $ProjectPython -m PyInstaller --noconfirm --clean --onedir --windowed --name "Учёт времени" client.py
    if ($LASTEXITCODE -ne 0) { throw "Ошибка сборки клиента." }
    & $ProjectPython -m PyInstaller --noconfirm --clean --onedir --console --add-data "schema.sql:." --name "Сервер учёта" server.py
    if ($LASTEXITCODE -ne 0) { throw "Ошибка сборки сервера." }
    & $ProjectPython -m PyInstaller --noconfirm --clean --onefile --windowed --name "Запустить программу" launcher.py
    if ($LASTEXITCODE -ne 0) { throw "Ошибка сборки общего запуска." }
    Write-Host "Сборки созданы в dist. Переносите каждую папку целиком, включая _internal."
    Write-Host "Для обычной работы откройте dist\Запустить программу.exe — сервер и клиент запустятся автоматически."
} finally {
    Pop-Location
}
