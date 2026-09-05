#!/bin/bash
# Запуск мастера прошивки в терминале.
# Linux-аналог run.zsh и «Прошивальщик.command» из macOS-версии:
# двойной щелчок по этому файлу открывает терминал и запускает мастер.
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
    echo ""
    echo "На этом компьютере нет python3. Он ставится одной командой:"
    echo ""
    echo "    Debian/Ubuntu:  sudo apt install python3 python3-venv"
    echo "    Fedora:         sudo dnf install python3"
    echo "    Arch:           sudo pacman -S python"
    echo ""
    read -rp "Нажмите Enter, чтобы закрыть окно. "
    exit 1
fi

python3 flasher.py
