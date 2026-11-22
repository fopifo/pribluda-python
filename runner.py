"""
runner.py - Исполнитель (Руки)
==============================
Умеет:
1. watch - следить за файлом и отправлять его изменения в чат.
2. send  - отправить текст из файла в чат.
3. run   - выполнить команду в терминале, перехватить вывод и отправить в чат.
"""
import sys
import time
import pyperclip
import pyautogui
import pygetwindow as gw
import subprocess
from pathlib import Path

# Настройки для Яндекса
WINDOW_TITLE = "DeepSeek"  # Слово в заголовке вкладки (оставляем, так как чат тот же)
INPUT_X = 315
INPUT_Y = 615

def find_and_activate_window(title_substring: str) -> bool:
    try:
        windows = gw.getWindowsWithTitle(title_substring)
        if not windows:
            print(f"[runner] Окно с заголовком '{title_substring}' не найдено.")
            return False
        
        win = windows[0]
        try:
            if win.isMinimized:
                win.restore()
            win.activate()
        except Exception:
            pass 
        
        time.sleep(1.5) 
        return True
    except Exception as e:
        print(f"[runner] Ошибка при активации окна: {e}")
        return False

def send_to_chat(text: str):
    if len(text) > 4000:
        print("[runner] Текст слишком длинный, обрезаю...")
        text = text[:2000] + "\n\n... [ТЕКСТ ОБРЕЗАН] ...\n\n" + text[-2000:]

    print(f"[runner] Ищу окно '{WINDOW_TITLE}'...")
    if not find_and_activate_window(WINDOW_TITLE):
        return

    print(f"[runner] Кликаю в поле ввода ({INPUT_X}, {INPUT_Y})...")
    pyautogui.click(INPUT_X, INPUT_Y)
    time.sleep(1.0) 

    print("[runner] Вставляю текст...")
    pyperclip.copy(text)
    pyautogui.hotkey('ctrl', 'v')
    
    # Увеличенная пауза для Яндекса
    time.sleep(2.5) 

    print("[runner] Нажимаю Enter...")
    pyautogui.press('enter')
    
    # Ждем отправки на сервер
    time.sleep(4.0) 
    print("[runner] Отправлено.")

def watch_file(filepath: str):
    path = Path(filepath)
    if not path.exists():
        path.touch()
    
    last_mtime = path.stat().st_mtime
    print(f"[runner] Слежу за файлом: {filepath}")
    
    while True:
        try:
            current_mtime = path.stat().st_mtime
            if current_mtime > last_mtime:
                time.sleep(1.0) 
                print(f"[runner] Файл обновился! Читаю...")
                try:
                    content = path.read_text(encoding='utf-8')
                    if content.strip():
                        send_to_chat(content)
                except Exception as read_err:
                    print(f"[runner] Ошибка чтения: {read_err}")
                last_mtime = current_mtime
            time.sleep(1)
        except KeyboardInterrupt:
            print("\n[runner] Остановлен.")
            break
        except Exception as e:
            print(f"[runner] Ошибка: {e}")
            time.sleep(2)

def run_command(command: str):
    print(f"[runner] Выполняю команду: {command}")
    
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=600
        )
        
        output = result.stdout + result.stderr
        
        if not output.strip():
            output = "Команда выполнена, но не вернула никакого текста (пустой вывод)."

        print("[runner] Команда завершена. Отправляю результат в чат...")
        send_to_chat(output)
        
    except subprocess.TimeoutExpired:
        send_to_chat(f"ОШИБКА: Команда превысила лимит времени (10 минут).\nКоманда: {command}")
    except Exception as e:
        send_to_chat(f"ОШИБКА выполнения команды:\n{e}\n\nКоманда: {command}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Использование:")
        print("  python runner.py watch <файл>")
        print("  python runner.py send <файл>")
        print("  python runner.py run <команда>")
        sys.exit(1)
        
    command = sys.argv[1]
    arg = sys.argv[2]
    
    if command == "watch":
        watch_file(arg)
    elif command == "send":
        try:
            content = Path(arg).read_text(encoding='utf-8')
            send_to_chat(content)
        except FileNotFoundError:
            print(f"[runner] Ошибка: файл {arg} не найден.")
    elif command == "run":
        full_cmd = " ".join(sys.argv[2:])
        run_command(full_cmd)
    else:
        print(f"Неизвестная команда: {command}")