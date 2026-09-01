#!/usr/bin/env python3
"""Пики для волновой формы. Кладёт peaks.json рядом с аудио.

  python3 make_peaks.py audio.wav [число_столбиков]

Значения нормированы в 0..1. Считает ffmpeg, разбор — array из stdlib.
"""
import array
import json
import subprocess
import sys
from pathlib import Path

RATE = 4000  # достаточно для картинки


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    audio = Path(sys.argv[1]).expanduser()
    buckets = int(sys.argv[2]) if len(sys.argv) > 2 else 1200
    if not audio.is_file():
        print(f"нет файла: {audio}")
        return 1

    proc = subprocess.run(
        ["ffmpeg", "-v", "quiet", "-i", str(audio),
         "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
        capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        print("ffmpeg не смог прочитать аудио")
        return 1

    samples = array.array("h")
    samples.frombytes(proc.stdout[: len(proc.stdout) // 2 * 2])
    if not samples:
        return 1

    step = max(1, len(samples) // buckets)
    peaks = []
    for i in range(0, len(samples), step):
        chunk = samples[i:i + step]
        peaks.append(max(abs(min(chunk)), abs(max(chunk))) / 32768)

    top = max(peaks) or 1.0
    peaks = [round(min(1.0, p / top), 3) for p in peaks]
    (audio.parent / "peaks.json").write_text(json.dumps(peaks), encoding="utf-8")
    print(f"  пики: {len(peaks)} столбиков → {audio.parent/'peaks.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
