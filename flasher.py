#!/usr/bin/env python3
"""Мастер прошивки адаптера постоянного тока (DC АДАПТЕР, метка QD в эфире).

Порт на Linux исходной версии для macOS (OllyCat/gbt-p6-flasher).
Ведёт человека по шагам и на каждом спрашивает согласие. Сам ничего не трогает,
пока не ответишь «да». Сторонних библиотек в системе не требует: всё, что
нужно для Bluetooth, ставит себе в отдельную папку рядом.

Запуск:  ./run.sh   или   python3 flasher.py
"""
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(HERE, ".venv")
WORKER = os.path.join(HERE, "ble_worker.py")
LOG = os.path.join(HERE, "worker_log.txt")
FIRMWARE = os.path.join(HERE, "firmware")

B = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
OFF = "\033[0m"

def title(text):
    print(f"\n{B}{text}{OFF}")
    print(DIM + "─" * min(len(text), 70) + OFF)

def info(text=""):
    print(text)

def warn(text):
    print(f"{YELLOW}{text}{OFF}")

def fail(text):
    print(f"\n{RED}{text}{OFF}")

def ok(text):
    print(f"{GREEN}{text}{OFF}")

def ask(question, default_yes=True):
    hint = "Д/н" if default_yes else "д/Н"
    while True:
        try:
            a = input(f"\n{B}{question}{OFF} [{hint}] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            bye("Отменено.")
        if not a:
            return default_yes
        if a in ("д", "да", "y", "yes", "l"):  # «l» — «д» в латинской раскладке
            return True
        if a in ("н", "нет", "n", "no", "y."):
            return False
        info("Ответьте «д» или «н».")

def choose(question, options):
    """options — список (подпись, значение)."""
    print(f"\n{B}{question}{OFF}")
    for i, (label, _) in enumerate(options, 1):
        print(f"  {i}. {label}")
    while True:
        try:
            a = input(f"\nНомер (1–{len(options)}): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            bye("Отменено.")
        if a.isdigit() and 1 <= int(a) <= len(options):
            return options[int(a) - 1][1]
        info("Введите номер из списка.")

def bye(text, code=0):
    print(f"\n{text}\n")
    try:
        input("Нажмите Enter, чтобы закрыть окно. ")
    except (EOFError, KeyboardInterrupt):
        print()
    sys.exit(code)

# ── подготовка окружения ────────────────────────────────────────────────────

def venv_python():
    return os.path.join(VENV, "bin", "python3")

def prepare():
    """Ставит bleak в свою папку. В отличие от macOS, на Linux не нужно
    собирать приложение-обёртку: к радио пускает BlueZ через D-Bus,
    а не система разрешений с вопросами про Bluetooth."""
    if os.path.exists(venv_python()):
        return True

    title("Шаг 1. Разовая подготовка")
    info("Первый запуск требует небольшой подготовки. Вот что будет сделано,")
    info("всё — внутри этой папки, система не затрагивается:")
    info()
    info("  • рядом появится папка .venv — отдельный питон для этой задачи;")
    info("  • в неё загрузится библиотека bleak (работа с Bluetooth), нужен интернет.")
    info()
    info("Занимает меньше минуты. Повторно это не понадобится.")
    if not ask("Продолжаем?"):
        bye("Хорошо, ничего не делаю.")

    if shutil.which("bluetoothctl") is None:
        fail("В системе нет пакета BlueZ — именно с ним работает Bluetooth.")
        info("Поставьте его одной командой и запустите программу снова:")
        info("    Debian/Ubuntu:  sudo apt install bluez")
        info("    Fedora:         sudo dnf install bluez")
        info("    Arch:           sudo pacman -S bluez bluez-utils")
        return False

    info("\nГотовлю питон…")
    r = subprocess.run([sys.executable, "-m", "venv", VENV],
                       capture_output=True, text=True)
    if r.returncode:
        fail("Не удалось создать окружение питона.")
        if "venv" in r.stderr or "ensurepip" in r.stderr:
            info("На Debian/Ubuntu модуль venv ставится отдельным пакетом:")
            info("    sudo apt install python3-venv")
        info(r.stderr.strip()[:500])
        return False

    info("Загружаю библиотеку Bluetooth…")
    r = subprocess.run([venv_python(), "-m", "pip", "install", "-q",
                        "--disable-pip-version-check", "bleak"],
                       capture_output=True, text=True)
    if r.returncode:
        fail("Не удалось загрузить библиотеку bleak.")
        info("Обычно это значит, что нет интернета. Подключитесь и запустите снова.")
        info(DIM + r.stderr.strip()[-400:] + OFF)
        return False
    ok("Подготовка закончена.")
    return True

# ── запуск Bluetooth-части ──────────────────────────────────────────────────

def worker_alive():
    # Через ps, а не pgrep: pgrep -f не находит процесс, если в пути к нему
    # есть кириллица, и молча отвечает «нет такого». У ps на Linux другие
    # ключи, чем на macOS: «-eo args» вместо «-Ao command».
    out = subprocess.run(["ps", "-eo", "args"],
                         capture_output=True, text=True, errors="replace").stdout
    return "ble_worker.py" in out

def run_worker(args, timeout, quiet=False):
    """Запускает ble_worker.py в питоне из .venv и показывает его вывод живьём.

    Вернёт список строк-меток. Кроме ##FOUND / ##DONE / ##FAIL от самого
    работника, может добавить ##DIED — это когда процесс исчез, ничего не
    сказав (на Linux так бывает, если упал BlueZ или адаптер отключился).

    На macOS здесь был «open -n -a BLEBridge.app --args …»: без приложения-
    обёртки система убивала процесс, тронувший Bluetooth. На Linux работник —
    обычный фоновый процесс, запускается напрямую.
    """
    if os.path.exists(LOG):
        os.remove(LOG)
    subprocess.Popen([venv_python(), WORKER, *args],
                     stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL,
                     start_new_session=True)

    markers, pos = [], 0
    deadline = time.time() + timeout
    grace = time.time() + 6  # столько ждём, пока процесс вообще заведётся

    def drain():
        nonlocal pos
        if not os.path.exists(LOG):
            return False
        with open(LOG, encoding="utf-8", errors="replace") as f:
            f.seek(pos)
            chunk = f.read()
            pos = f.tell()
        for line in chunk.splitlines():
            if line.startswith("##"):
                markers.append(line)
                if line.startswith(("##DONE", "##FAIL")):
                    return True
            elif not quiet:
                info(" " + line if line.strip() else "")
        return False

    while time.time() < deadline:
        if drain():
            return markers
        if time.time() > grace and not worker_alive():
            time.sleep(0.5)
            if drain():  # вдруг успел дописать перед выходом
                return markers
            markers.append("##DIED")
            return markers
        time.sleep(0.3)

    markers.append("##FAIL Устройство не ответило вовремя.")
    return markers

def marker_error(markers):
    for m in markers:
        if m.startswith("##FAIL"):
            return m[len("##FAIL"):].strip() or "неизвестная причина"
    return None

def died(markers):
    return any(m.startswith("##DIED") for m in markers)

def bluetooth_access():
    """Проверяет, что радио доступно. На Linux система не показывает вопросов
    про Bluetooth — вместо этого мешать могут выключенный адаптер, rfkill
    или незапущенный BlueZ."""
    title("Шаг 2. Доступ к Bluetooth")
    info("Проверяю, видит ли система Bluetooth-адаптер.")
    markers = run_worker(["ping"], timeout=40, quiet=True)

    if not died(markers) and not marker_error(markers):
        ok("Доступ есть.")
        return True

    fail("Сканировать эфир пока не получается.")
    info("На Linux это почти всегда одно из четырёх:")
    info()
    info("  • адаптер выключен программно. Включите его:")
    info("        rfkill unblock bluetooth")
    info("        bluetoothctl power on")
    info("  • не запущена служба BlueZ:")
    info("        sudo systemctl start bluetooth")
    info("  • адаптер занят другим приложением — закройте его;")
    info("  • Bluetooth-адаптера в компьютере нет или он вынут.")
    info()
    info("Подробности ошибки — в файле worker_log.txt рядом с программой.")
    info("Исправьте причину и запустите программу заново.")
    return False

# ── шаги мастера ────────────────────────────────────────────────────────────

def pick_firmware():
    title("Шаг 3. Какую прошивку заливать")
    options = []
    ru = os.path.join(FIRMWARE, "русская-V05.15.hex")
    cn = os.path.join(FIRMWARE, "заводская-V05.15.hex")
    if os.path.exists(ru):
        options.append(("Русская — весь экран на русском языке", ru))
    if os.path.exists(cn):
        options.append(("Заводская китайская — вернуть как было", cn))
    options.append(("Свой файл .hex", "?"))
    options.append(("Ничего не делать, выйти", None))

    choice = choose("Что заливаем?", options)
    if choice is None:
        bye("Хорошо, выходим.")
    if choice != "?":
        return choice

    info("\nПеретащите файл .hex в это окно и нажмите Enter,")
    info("либо вставьте путь к нему.")
    try:
        path = input("\nФайл: ").strip().strip("'\"")
    except (EOFError, KeyboardInterrupt):
        bye("Отменено.")
    path = os.path.expanduser(path.replace("\\ ", " "))
    if not os.path.isfile(path):
        fail("Такого файла нет.")
        return pick_firmware()
    if not path.lower().endswith(".hex"):
        warn("Файл не оканчивается на .hex — это точно прошивка?")
        if not ask("Всё равно взять его?", default_yes=False):
            return pick_firmware()
    return path

def find_device():
    title("Шаг 4. Поиск устройства")
    info("Сейчас поищу адаптер по Bluetooth. Перед этим убедитесь:")
    info()
    info("  • адаптер подключён к зарядной станции и на нём горит экран;")
    info("  • он не подключён сейчас к телефону — отключитесь в приложении;")
    info("  • Bluetooth на этом компьютере включён.")
    if not ask("Искать?"):
        bye("Хорошо, выходим.")

    markers = run_worker(["scan"], timeout=90)
    if died(markers):
        fail("Программа потеряла связь с Bluetooth-подсистемой.")
        info("Проверьте, что служба запущена: sudo systemctl start bluetooth,")
        info("и что адаптер не заблокирован: rfkill list.")
        return None
    err = marker_error(markers)
    if err:
        fail(f"Поиск не удался: {err}")
        return None

    found = [m[len("##FOUND"):].strip().split("\t") for m in markers
             if m.startswith("##FOUND")]
    if not found:
        fail("Адаптер не найден.")
        info("Чаще всего это значит одно из трёх:")
        info("  • устройство обесточено — на нём должен гореть экран;")
        info("  • оно занято телефоном — закройте приложение и отключитесь;")
        info("  • оно слишком далеко — подойдите ближе, метра достаточно.")
        return None

    if len(found) == 1:
        addr, name = found[0]
        ok(f"\nНашёлся: {name}")
        return addr if ask("Это он? Заливаем в него?") else None

    return choose("Нашлось несколько. В какое заливать?",
                  [(f"{n}  {a}", a) for a, n in found] + [("Ни в какое", None)])

def confirm_flash(path, name_hint):
    title("Шаг 5. Последняя проверка")
    info(f"Файл прошивки: {B}{os.path.basename(path)}{OFF}")
    info(f"Размер файла:  {os.path.getsize(path) // 1024} КБ")
    info()
    warn("Дальше начнётся запись. Что важно знать:")
    info()
    info("  • займёт около двух с половиной минут;")
    info("  • не выключайте адаптер и не закрывайте это окно;")
    info("  • ноутбук не должен уснуть — не закрывайте крышку;")
    info("  • если запись прервётся, адаптер останется без прошивки, но его")
    info("    можно будет прошить заново этой же программой: загрузчик цел;")
    info("  • после обновления пароль устройства станет восемь нулей.")
    return ask("Начинаем запись?", default_yes=False)

def main():
    os.system("")  # включает цвета в некоторых терминалах
    print()
    print(f"{B}Прошивальщик адаптера постоянного тока{OFF}")
    print(DIM + "Обновление прошивки по Bluetooth, без проводов и без телефона." + OFF)
    print()
    info("Программа умеет две вещи: залить в адаптер русскую прошивку")
    info("и вернуть заводскую китайскую. На каждом шаге будет спрашивать согласие,")
    info("так что просто читайте и отвечайте.")

    if not sys.platform.startswith("linux"):
        bye("Эта версия рассчитана на Linux. Для macOS — оригинал: "
            "github.com/OllyCat/gbt-p6-flasher", 1)
    if not os.path.exists(WORKER):
        bye("Рядом нет файла ble_worker.py — распакуйте папку целиком.", 1)

    if not prepare():
        bye("Не получилось подготовиться. Ничего не изменено.", 1)

    if not bluetooth_access():
        bye("Без доступа к Bluetooth продолжать нечем. Устройство не тронуто.", 1)

    firmware = pick_firmware()

    address = find_device()
    if not address:
        bye("Заливка не начата, устройство не тронуто.")

    if not confirm_flash(firmware, address):
        bye("Хорошо, ничего не залито.")

    title("Шаг 6. Запись")
    markers = run_worker(["flash", firmware, address], timeout=900)
    if died(markers):
        fail("Связь с Bluetooth оборвалась на середине.")
        info("Включите адаптер заново и повторите — прошивается он и после сбоя.")
        bye("", 1)
    err = marker_error(markers)
    if err:
        fail(f"Не получилось: {err}")
        info()
        info("Что делать: включите адаптер заново и запустите программу ещё раз.")
        info("Устройство прошивается повторно даже после неудачной попытки.")
        bye("", 1)

    ok("\nПрошивка записана и сверена устройством.")
    title("Осталось одно действие")
    info("Новая прошивка включается только после перезапуска питания.")
    info()
    info(f"  {B}Отключите адаптер от станции и подключите снова.{OFF}")
    info()
    info("После этого на экране появится новая заставка.")
    info("Пароль устройства сброшен на восемь нулей: 00000000")
    bye("Готово. Спасибо!")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        bye("\nПрервано с клавиатуры. Если запись уже шла, повторите заливку.")
