#!/usr/bin/env python3
"""Проверка, что в запись идёт звук звонка, а не только микрофон.

  python3 callcheck.py live audio.wav ffmpeg.pid   следит, пока идёт запись
  python3 callcheck.py summary audio.wav           итог после стопа

Podslushka In собран как [микрофон, BlackHole 2ch], поэтому звук звонка —
два последних канала WAV. Когда в BlackHole ничего не играет, там цифровой
ноль, и порога в -70 дБ хватает, чтобы отличить звонок от тишины.

Файл читается сырым: пока ffmpeg пишет, размер блока данных в заголовке ещё
не проставлен, поэтому граница данных берётся по размеру файла.
live молчит, если звук звонка есть, и один раз предупреждает, если его нет:
через EARLY_WARN секунд, если в микрофоне уже звучит голос (разговор идёт,
а собеседников не слышно), и через WARN_AFTER секунд в любом случае. Пока
предупреждение в силе, рядом с pid-файлом лежит флаг nocall — по нему панель
показывает «Нет звонка». summary дописывает в meta.json call_audio_seconds
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
MIC_VOICE = 600         # голос в микрофоне, около -35 дБ: шум комнаты тише
EARLY_WARN = 15         # секунд тишины звонка при голосе в микрофоне
WARN_AFTER = 60         # секунд без звука звонка до предупреждения в любом случае
POLL = 5                # как часто смотреть, секунд: предупреждение на 15–20 с
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
    """Наибольшая амплитуда в двух последних каналах блока — звонок."""
    return peak_of(block, ch, (ch - 2, ch - 1))


def mic_peak(block: bytes, ch: int) -> int:
    """Наибольшая амплитуда в каналах микрофона — все, кроме двух последних."""
    return peak_of(block, ch, range(ch - 2))


def peak_of(block: bytes, ch: int, chans) -> int:
    a = array.array("h")
    a.frombytes(block[:len(block) - len(block) % (2 * ch)])
    if sys.byteorder != "little":
        a.byteswap()
    peak = 0
    for c in chans:
        part = a[c::ch]
        if part:
            peak = max(peak, max(part), -min(part))
    return peak


def notify(text: str, sound: bool = False) -> None:
    tail = ' sound name "Funk"' if sound else ""
    subprocess.run(["osascript", "-e",
                    f'display notification "{text}" with title "Подслушка"{tail}'],
                   capture_output=True)


def alive(pid_file: Path) -> bool:
    try:
        os.kill(int(pid_file.read_text().strip()), 0)
        return True
    except (OSError, ValueError):
        return False


def live(wav: Path, pid_file: Path) -> int:
    started = time.time()
    flag = pid_file.parent / "nocall"
    flag.unlink(missing_ok=True)
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
    voice = False
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
                flag.unlink(missing_ok=True)
                notify("Звук звонка пошёл в запись")
            return 0
        voice = voice or mic_peak(block, ch) > MIC_VOICE
        waited = time.time() - started
        if not warned and (waited >= WARN_AFTER or (voice and waited >= EARLY_WARN)):
            notify("Звука звонка нет, пишется только микрофон. "
                   "Выбери в звонке динамик Podslushka Out", sound=True)
            flag.touch()
            print(f"звука звонка нет {int(waited)} с"
                  f"{', голос в микрофоне есть' if voice else ''}", flush=True)
            warned = True
    flag.unlink(missing_ok=True)
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
