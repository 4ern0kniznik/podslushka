#!/usr/bin/env python3
"""Проверка, что в запись идёт звук звонка, а не только микрофон.

  python3 callcheck.py live audio.wav ffmpeg.pid   следит, пока идёт запись
  python3 callcheck.py summary audio.wav           итог после стопа

Podslushka In собран как [микрофон, BlackHole 2ch], поэтому звук звонка —
два последних канала WAV. Когда в BlackHole ничего не играет, там цифровой
ноль, и порога в -70 дБ хватает, чтобы отличить звонок от тишины.

Файл читается сырым: пока ffmpeg пишет, размер блока данных в заголовке ещё
не проставлен, поэтому граница данных берётся по размеру файла.
live молчит, если звук звонка есть, и один раз предупреждает, если его нет
дольше WARN_AFTER секунд. summary дописывает в meta.json call_audio_seconds
и выходит с кодом 3, если звука звонка не было совсем.
"""
import array
import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

THRESHOLD = 10          # из 32767, это около -70 дБ
WARN_AFTER = 60         # секунд без звука звонка до предупреждения
POLL = 10               # как часто смотреть, секунд
TAIL = 5                # сколько последних секунд слушать за раз


def header(path: Path):
    """Каналы, частота и смещение начала данных. None, если это не наш WAV."""
    with path.open("rb") as f:
        head = f.read(65536)
    if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        return None
    pos, fmt = 12, None
    while pos + 8 <= len(head):
        cid, size = head[pos:pos + 4], struct.unpack("<I", head[pos + 4:pos + 8])[0]
        body = pos + 8
        if cid == b"fmt ":
            _, ch, rate = struct.unpack("<HHI", head[body:body + 8])
            bits = struct.unpack("<H", head[body + 14:body + 16])[0]
            fmt = (ch, rate, bits)
        elif cid == b"data":
            if fmt is None or fmt[2] != 16:
                return None
            return fmt[0], fmt[1], body
        pos = body + size + (size & 1)
    return None


def call_peak(block: bytes, ch: int) -> int:
    """Наибольшая амплитуда в двух последних каналах блока."""
    a = array.array("h")
    a.frombytes(block[:len(block) - len(block) % (2 * ch)])
    if sys.byteorder != "little":
        a.byteswap()
    peak = 0
    for c in (ch - 2, ch - 1):
        part = a[c::ch]
        if part:
            peak = max(peak, max(part), -min(part))
    return peak


def notify(text: str) -> None:
    subprocess.run(["osascript", "-e",
                    f'display notification "{text}" with title "Подслушка"'],
                   capture_output=True)


def alive(pid_file: Path) -> bool:
    try:
        os.kill(int(pid_file.read_text().strip()), 0)
        return True
    except (OSError, ValueError):
        return False


def live(wav: Path, pid_file: Path) -> int:
    started = time.time()
    hdr = None
    while hdr is None:
        if not alive(pid_file) or time.time() - started > 30:
            return 0
        time.sleep(1)
        if wav.is_file():
            hdr = header(wav)
    ch, rate, data = hdr
    if ch < 3:
        return 0  # не агрегатное устройство, звонку взяться неоткуда
    frame = 2 * ch
    warned = False
    while alive(pid_file):
        time.sleep(POLL)
        size = wav.stat().st_size
        want = TAIL * rate * frame
        start = max(data, data + (size - data - want) // frame * frame)
        with wav.open("rb") as f:
            f.seek(start)
            block = f.read(size - start)
        if call_peak(block, ch) > THRESHOLD:
            if warned:
                notify("Звук звонка пошёл в запись")
            return 0
        if not warned and time.time() - started >= WARN_AFTER:
            notify("Звука звонка нет, пишется только микрофон. "
                   "Выбери в звонке динамик Podslushka Out")
            print("звука звонка нет дольше минуты", flush=True)
            warned = True
    return 0


def summary(wav: Path) -> int:
    hdr = header(wav)
    if hdr is None:
        print("  проверка звука звонка: не разобрал WAV")
        return 0
    ch, rate, data = hdr
    if ch < 3:
        return 0
    frame = 2 * ch
    second = rate * frame
    voiced = 0
    with wav.open("rb") as f:
        f.seek(data)
        while block := f.read(second):
            if call_peak(block, ch) > THRESHOLD:
                voiced += 1
    meta_path = wav.parent / "meta.json"
    meta = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except ValueError:
            meta = {}
    meta["call_audio_seconds"] = voiced
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  звук звонка в записи: {voiced} с")
    return 0 if voiced else 3


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] == "live":
        return live(Path(sys.argv[2]), Path(sys.argv[3]))
    if len(sys.argv) == 3 and sys.argv[1] == "summary":
        return summary(Path(sys.argv[2]))
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
