@echo off
chcp 65001 >nul
echo ====================================================
echo  9Router Antigravity Tray Monitor - Установка
echo ====================================================
echo.

echo [1/3] Проверка Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ОШИБКА] Python не найден в PATH. Установите Python 3.9+ с сайта python.org
    pause
    exit /b 1
)

echo [2/3] Установка зависимостей (pystray, pillow)...
pip install -r requirements.txt
if errorlevel 1 (
    echo [ОШИБКА] Не удалось установить зависимости.
    pause
    exit /b 1
)

echo [3/3] Настройка автозагрузки...
set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SHORTCUT=%STARTUP_DIR%\9Router-Antigravity-Tray.vbs"

copy /Y "%~dp0run.vbs" "%SHORTCUT%" >nul
if errorlevel 1 (
    echo [ПРЕДУПРЕЖДЕНИЕ] Не удалось скопировать в автозагрузку. Запускайте через run.vbs вручную.
) else (
    echo [OK] Автозагрузка настроена: %SHORTCUT%
)

echo.
echo ====================================================
echo  Установка успешно завершена!
echo  Запускаем монитор в фоновом режиме...
echo ====================================================
wscript.exe "%~dp0run.vbs"
exit /b 0
