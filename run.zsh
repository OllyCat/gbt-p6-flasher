#!/bin/zsh
# Двойной щелчок по этому файлу открывает Терминал и запускает мастер прошивки.
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo ""
    echo "На этом компьютере нет python3."
    echo "Он ставится одной командой — выполните в Терминале:"
    echo ""
    echo "    xcode-select --install"
    echo ""
    echo "Согласитесь с установкой, дождитесь конца и запустите этот файл снова."
    echo ""
    read "?Нажмите Enter, чтобы закрыть окно. "
    exit 1
fi

python3 flasher.py
