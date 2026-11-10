#!/usr/bin/env python3
"""Дорожки «Они / Я» и сторож звонка: ~/.podslushka/venv/bin/python tests/test_tracks.py

Всё на синтетике: голос say в нужных каналах WAV. Личные записи не нужны.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "lib"
sys.path.insert(0, str(LIB))
from tracks import Tracks  # noqa: E402

ok = True
PY = sys.executable
tmp = Path(tempfile.mkdtemp(prefix="pod-test-"))


def check(name, got, want):
    global ok
    print(("ok   " if got == want else "FAIL ") + f"{name}" + ("" if got == want else f": {got!r} ≠ {want!r}"))
    ok &= got == want


# ---------- дорожки «Они / Я» ----------
def say(text, out):
    subprocess.run(["say", "-v", "Milena", "-o", str(out), text], check=True, stdin=subprocess.DEVNULL)


say("Добрый день, это собеседник по звонку.", tmp / "they.aiff")
say("А это мой голос в микрофон.", tmp / "me.aiff")
wav = tmp / "rec.wav"
# 0–1 с тишина, 1 с: «Они» (звонок L/R + эхо в микрофоне −14 дБ через 30 мс), 6 с: «Я» (только микрофон)
fc = ("[0]aresample=48000,adelay=1000:all=1,apad=whole_dur=12[t];[t]asplit=3[tl][tr][te];"
      "[te]volume=-14dB,adelay=30:all=1[echo];"
      "[1]aresample=48000,adelay=6000:all=1,apad=whole_dur=12[me];"
      "[me][echo]amix=inputs=2:normalize=0[mic];"
      "anoisesrc=color=pink:amplitude=0.003:r=48000:d=12[room];[mic][room]amix=inputs=2:normalize=0[micn];"
      "[micn][tl][tr]join=inputs=3:channel_layout=2.1[o]")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-i", str(tmp / "they.aiff"), "-i", str(tmp / "me.aiff"),
                "-filter_complex", fc, "-map", "[o]", "-t", "12", "-c:a", "pcm_s16le", str(wav)], check=True)
subprocess.run([PY, str(LIB / "tracks.py"), "envelope", str(wav), str(tmp / "tracks.json")],
               check=True, capture_output=True)
t = Tracks(json.loads((tmp / "tracks.json").read_text()))
check("дорожки: звонок найден", t.has_call, True)
check("дорожки: 1,2–3,5 с — Они", t.who(1.2, 3.5), "Они")
check("дорожки: 6,2–8 с — Я", t.who(6.2, 8.0), "Я")
check("дорожки: тишина 0–0,8 с — без пометки", t.who(0, 0.8), None)
nocall = Tracks({"step": 0.1, "mic": [-30] * 100, "call": [-90] * 100})
check("дорожки: без звонка пометок нет", nocall.who(1, 5), None)

# ---------- сторож не уходит за следующей записью ----------
import callcheck as CC  # noqa: E402
pidf = tmp / "ffmpeg.pid"
for mod, name in ((CC, "проверка звонка"),):
    a = subprocess.Popen(["sleep", "30"])
    pidf.write_text(str(a.pid))
    first = mod.alive(pidf)
    a.kill(); a.wait()
    b = subprocess.Popen(["sleep", "30"])  # следующая запись переписала pid-файл
    pidf.write_text(str(b.pid))
    check(f"{name}: сторож видит свою запись, а не следующую", (first, mod.alive(pidf)), (True, False))
    b.kill(); b.wait()
    mod._PIDS.clear()

print("\nвсё прошло" if ok else "\nЕСТЬ ПАДЕНИЯ")
print("рабочая папка:", tmp)
sys.exit(0 if ok else 1)
