#!/usr/bin/env python3
"""Bluetooth-часть прошивальщика, порт на Linux.

Отдельный файл потому, что на macOS его приходилось запускать внутри
.app-обёртки. На Linux обёртка не нужна: мастер (flasher.py) запускает этот
файл напрямую в питоне из .venv, а bleak разговаривает с BlueZ по D-Bus.

Запускается не человеком, а мастером:

    ble_worker.py ping                     проверка доступа к Bluetooth
    ble_worker.py scan
    ble_worker.py flash <файл.hex> <адрес устройства>

Вывод пишется в worker_log.txt рядом с этим файлом. Строки, начинающиеся
с ##, мастер разбирает сам:

    ##FOUND <адрес>\t<имя>      найдено устройство
    ##INFO <чип>\t<блок>        сведения о микросхеме
    ##DONE                      всё получилось
    ##FAIL <причина>            не получилось
"""
import asyncio
import os
import sys
import time

from bleak import BleakScanner, BleakClient

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = open(os.path.join(HERE, "worker_log.txt"), "w", buffering=1)

NAME_PREFIX = "QD:"
CH_OTA = "0000fee1-0000-1000-8000-00805f9b34fb"

CMD_PROGRAM, CMD_ERASE, CMD_VERIFY, CMD_END, CMD_INFO = 0x80, 0x81, 0x82, 0x83, 0x84
IAP_LEN = 20
ADDRESS_BASE = 16

CHIPS = {(0x83, 0x00): "CH583", (0x08, 0x02): "CH32V208", (0x08, 0xF2): "CH32F208",
         (0x79, 0x00): "CH579", (0x73, 0x00): "CH573", (0x92, 0x00): "CH592"}

def say(msg):
    LOG.write(msg + "\n")

def parse_hex(path):
    """Intel HEX -> (начальный адрес, сплошной массив байт)."""
    data, base = {}, 0
    for line in open(path):
        line = line.strip()
        if not line.startswith(":"):
            continue
        b = bytes.fromhex(line[1:])
        n, off, typ, payload = b[0], (b[1] << 8) | b[2], b[3], b[4:4 + b[0]]
        if typ == 0:
            for i, v in enumerate(payload):
                data[base + off + i] = v
        elif typ == 2:
            base = ((payload[0] << 8) | payload[1]) * 16
        elif typ == 4:
            base = ((payload[0] << 8) | payload[1]) << 16
        elif typ == 1:
            break
    if not data:
        raise SystemExit("в файле нет ни одной записи Intel HEX")
    lo, hi = min(data), max(data)
    buf = bytearray(b"\xff" * (hi - lo + 1))
    for a, v in data.items():
        buf[a - lo] = v
    return lo, bytes(buf)

def cmd_fixed(code):
    b = bytearray(IAP_LEN)
    b[0], b[1] = code, IAP_LEN - 2
    return bytes(b)

def cmd_erase(addr, blocks):
    b = bytearray(IAP_LEN)
    b[0], b[1] = CMD_ERASE, 0
    a = addr // ADDRESS_BASE
    b[2], b[3] = a & 0xFF, (a >> 8) & 0xFF
    b[4], b[5] = blocks & 0xFF, (blocks >> 8) & 0xFF
    return bytes(b)

def cmd_data(code, addr, payload, data_len):
    b = bytearray(data_len)
    b[0], b[1] = code, data_len - 4
    a = addr // ADDRESS_BASE
    b[2], b[3] = a & 0xFF, (a >> 8) & 0xFF
    b[4:4 + len(payload)] = payload
    return bytes(b)

async def read_until(client, timeout):
    """Ответ появляется в характеристике не сразу — опрашиваем до таймаута."""
    deadline = time.time() + timeout
    while True:
        resp = bytes(await client.read_gatt_char(CH_OTA))
        if resp:
            return resp
        if time.time() > deadline:
            return b""
        await asyncio.sleep(0.2)

async def xfer(client, packet, timeout=3.0):
    await client.write_gatt_char(CH_OTA, packet, response=True)
    return await read_until(client, timeout)

async def do_ping():
    """Короткое касание радио: проверяет, что сканер BlueZ вообще заводится."""
    say("Проверяю доступ к Bluetooth…")
    scanner = BleakScanner()
    await scanner.start()
    await asyncio.sleep(2)
    await scanner.stop()
    say("##DONE")

async def do_scan():
    say("Ищу устройства в эфире, это занимает около 15 секунд…")
    found = {}

    def cb(dev, adv):
        name = adv.local_name or dev.name or ""
        if name and dev.address not in found:
            found[dev.address] = name

    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    await asyncio.sleep(15)
    await scanner.stop()

    ours = {a: n for a, n in found.items() if n.startswith(NAME_PREFIX)}
    for addr, name in ours.items():
        say(f"##FOUND {addr}\t{name}")
    if not ours:
        say("Подходящих устройств не видно.")
        say("Рядом было видно вот что (для справки):")
        for addr, name in list(found.items())[:15]:
            say(f"  {name}")
    say("##DONE")

async def do_flash(hex_path, address):
    start, image = parse_hex(hex_path)
    say(f"Файл прошивки: {os.path.basename(hex_path)}")
    say(f"Адрес загрузки 0x{start:X}, размер {len(image)} байт.")

    say("Подключаюсь к устройству…")
    dev = None
    for d in await BleakScanner.discover(timeout=15):
        if d.address == address:
            dev = d
            break
    if dev is None:
        say("##FAIL Устройство пропало из эфира. Проверьте, что оно включено, и повторите.")
        return

    async with BleakClient(dev, timeout=25) as client:
        mtu = client.mtu_size
        names = [c.uuid for s in client.services for c in s.characteristics]
        if CH_OTA not in names:
            say("##FAIL Это устройство не умеет обновляться по этому каналу.")
            return
        say(f"Подключился, размер пакета {mtu} байт.")

        resp = await xfer(client, cmd_fixed(CMD_INFO), 5.0)
        if len(resp) != 20:
            say("##FAIL Устройство не ответило на запрос сведений.")
            return
        block = resp[5] | (resp[6] << 8)
        chip = CHIPS.get((resp[7], resp[8]), "неизвестная")
        say(f"##INFO {chip}\t{block}")
        say(f"Микросхема {chip}, блок стирания {block} байт.")

        block = block or 4096
        blocks = -(-len(image) // block)
        data_len = mtu - 3
        chunk = data_len - 4
        if chunk % ADDRESS_BASE:
            chunk -= chunk % ADDRESS_BASE

        say("")
        say(f"Шаг 1 из 3. Стираю старую прошивку ({blocks} блоков).")
        say("Это самая долгая часть, до минуты. Не выключайте устройство.")
        resp = await xfer(client, cmd_erase(start, blocks), 60.0)
        if not resp or resp[0] != 0:
            say("##FAIL Стереть не удалось. Устройство осталось со старой прошивкой.")
            return
        say("Стёрто.")

        for n, (phase, code) in enumerate((("Записываю", CMD_PROGRAM),
                                           ("Сверяю", CMD_VERIFY)), start=2):
            say("")
            say(f"Шаг {n} из 3. {phase} прошивку.")
            pos, last, t = 0, -1, time.time()
            while pos < len(image):
                part = image[pos:pos + chunk]
                await client.write_gatt_char(CH_OTA,
                                             cmd_data(code, start + pos, part, data_len),
                                             response=True)
                pos += len(part)
                pct = pos * 100 // len(image)
                if pct != last and pct % 10 == 0:
                    bar = "█" * (pct // 10) + "·" * (10 - pct // 10)
                    say(f"  [{bar}] {pct}%")
                    last = pct
            r = await read_until(client, 30.0)
            if not r or r[0] != 0:
                say(f"##FAIL Не прошёл шаг «{phase.lower()}». "
                    f"Повторите заливку, устройство сейчас без рабочей прошивки.")
                return
            say(f"  готово за {time.time() - t:.0f} секунд")

        await xfer(client, cmd_fixed(CMD_END), 10.0)
        say("")
        say("##DONE")

def main():
    args = sys.argv[1:]
    if not args:
        say("##FAIL Не сказано, что делать.")
        return
    try:
        if args[0] == "ping":
            asyncio.run(do_ping())
        elif args[0] == "scan":
            asyncio.run(do_scan())
        elif args[0] == "flash":
            asyncio.run(do_flash(args[1], args[2]))
        else:
            say(f"##FAIL Непонятная команда {args[0]!r}.")
    except SystemExit as e:
        say(f"##FAIL {e}")
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        if "DBus" in msg or "org.bluez" in msg:
            msg += " (похоже, не запущен BlueZ: sudo systemctl start bluetooth)"
        say(f"##FAIL {msg}")

main()
