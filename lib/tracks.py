#!/usr/bin/env python3
"""Дорожки «Они / Я»: кто говорил в каждой реплике.

  tracks.py envelope audio.wav tracks.json   громкость микрофона и звонка по 0,1 с
  tracks.py check <папка звонка>             сколько реплик чьи — для проверки

Podslushka In — это [микрофон…, BlackHole L, BlackHole R]: собеседники
приходят только в два последних канала, свой голос — только в микрофон.
После стопа WAV сводится в моно и удаляется, поэтому до сведения отсюда
снимается огибающая громкости обоих источников — несколько сотен КБ на час.
Расшифровка (transcribe.py) по ней подписывает каждую реплику.

Через динамики микрофон слышит и собеседника, но тише и с задержкой,
а в канале звонка своего голоса нет никогда. Поэтому решает канал звонка:
звучит он — «Они», молчит при живом микрофоне — «Я».
"""
import json
import sys
import wave
from pathlib import Path

STEP = 0.1  # с
FLOOR = -90  # дБ, цифровая тишина


def envelope(wav_path: Path, out: Path) -> int:
    import numpy as np

    with wave.open(str(wav_path), "rb") as w:
        ch, sw, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        if ch < 3 or sw != 2:
            print(f"  дорожек не разделить: каналов {ch}, нужно микрофон + 2 канала звонка")
            return 1
        block = int(rate * STEP)
        mic, call = [], []
        while True:
            raw = w.readframes(block)
            if not raw:
                break
            a = np.frombuffer(raw, dtype="<i2").reshape(-1, ch).astype(np.float32) / 32768
            m = np.sqrt(np.mean(a[:, : ch - 2] ** 2))
            c = np.sqrt(np.mean(a[:, ch - 2 :] ** 2))
            mic.append(round(max(FLOOR, 20 * np.log10(m + 1e-9))))
            call.append(round(max(FLOOR, 20 * np.log10(c + 1e-9))))
    out.write_text(json.dumps({"step": STEP, "mic": mic, "call": call}), encoding="utf-8")
    print(f"  дорожки «Они / Я»: {len(mic) * STEP / 60:.1f} мин → {out.name}")
    return 0


class Tracks:
    """Огибающая и пороги. who(start, end) → «Они», «Я» или None."""

    def __init__(self, data: dict):
        self.step = data["step"]
        self.mic, self.call = data["mic"], data["call"]
        # Порог — на 15 дБ выше собственного шума канала, но не ниже −50 дБ:
        # в BlackHole без звонка цифровая тишина, у микрофона — шум комнаты.
        self.mic_thr = max(-50, self._p10(self.mic) + 15)
        self.call_thr = max(-50, self._p10(self.call) + 15)
        on = sum(1 for c in self.call if c > self.call_thr)
        self.has_call = on > 0.01 * max(1, len(self.call))

    @staticmethod
    def _p10(xs: list) -> float:
        s = sorted(xs) or [FLOOR]
        return s[len(s) // 10]

    @classmethod
    def load(cls, folder: Path):
        p = folder / "tracks.json"
        if not p.is_file():
            return None
        try:
            return cls(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return None

    def who(self, start: float, end: float):
        if not self.has_call:  # звонка в записи нет — все реплики из микрофона
            return None
        a, b = int(start / self.step), max(int(start / self.step) + 1, int(end / self.step))
        they = me = 0
        for m, c in zip(self.mic[a:b], self.call[a:b]):
            if c > self.call_thr:
                they += 1
            elif m > self.mic_thr:
                me += 1
        if they == me == 0:
            return None
        return "Они" if they >= me else "Я"


def check(folder: Path) -> int:
    t = Tracks.load(folder)
    if t is None:
        print("нет tracks.json — запись без дорожек (старая или без канала звонка)")
        return 1
    srt = (folder / "transcript.srt").read_text(encoding="utf-8") if (folder / "transcript.srt").is_file() else ""
    n = {"Они": 0, "Я": 0, None: 0}
    for block in srt.strip().split("\n\n"):
        lines = block.splitlines()
        if len(lines) >= 3:
            n[lines[2].split(":", 1)[0] if lines[2].startswith(("Они:", "Я:")) else None] += 1
    print(f"звонок в записи: {'да' if t.has_call else 'нет'}; пороги: микрофон {t.mic_thr} дБ, звонок {t.call_thr} дБ")
    print(f"реплик: Они {n['Они']}, Я {n['Я']}, без пометки {n[None]}")
    return 0


def main() -> int:
    a = sys.argv[1:]
    if len(a) == 3 and a[0] == "envelope":
        return envelope(Path(a[1]), Path(a[2]))
    if len(a) == 2 and a[0] == "check":
        return check(Path(a[1]).expanduser())
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
