#!/usr/bin/env python3
"""Автокадры и дорожки «Они / Я»: ~/.podslushka/venv/bin/python tests/test_frames_tracks.py

Всё на синтетике: видео со «слайдами» и «камерой» собирает ffmpeg, звонок —
голос say в нужных каналах. Личные записи не нужны. Корзина — временная.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "lib"
sys.path.insert(0, str(LIB))
import frames as F  # noqa: E402
from tracks import Tracks  # noqa: E402

ok = True
PY = sys.executable


def check(name, got, want):
    global ok
    print(("ok   " if got == want else "FAIL ") + f"{name}" + ("" if got == want else f": {got!r} ≠ {want!r}"))
    ok &= got == want


# ---------- детектор на подписях ----------
def img(*rects, bg=20, fg=220):
    """Подпись 128×80: фон и залитые прямоугольники (x0, y0, x1, y1)."""
    px = bytearray([bg] * F.W * F.H)
    for x0, y0, x1, y1 in rects:
        for y in range(y0, y1):
            px[y * F.W + x0:y * F.W + x1] = bytes([fg] * (x1 - x0))
    return bytes(px)


A = img((8, 8, 120, 16))                      # шапка слайда
B = img((8, 8, 120, 16), (8, 24, 100, 56))    # тот же шаблон, другой текст
C = img((8, 8, 120, 16), (8, 24, 40, 72))
cursor = img((8, 8, 120, 16), (60, 60, 64, 64))  # курсор мыши — одна клетка
cam = [img((8, 8, 120, 16), (104, 24 + k % 2 * 8, 128, 80)) for k in range(2)]  # полоса камер справа
full = [img(bg=40 + k % 2 * 150) for k in range(2)]  # камера во весь экран


def run(seq):
    d = F.Detector()
    return [round(i * F.TICK, 2) for i, s in enumerate(seq) if d.feed(i * F.TICK, s)]


check("слайд встал на 1,5 с → один кадр", run([A] * 6), [1.5])
check("курсор мыши кадра не даёт", run([A] * 6 + [cursor, A] * 3), [1.5])
check("новый текст в том же шаблоне → второй кадр", run([A] * 6 + [B] * 6), [1.5, 6.0])
check("и третий", run([A] * 6 + [B] * 6 + [C] * 6), [1.5, 6.0, 10.5])
check("камера во весь экран кадров не даёт", run([full[k % 2] for k in range(16)]), [])
check("полоса камер сбоку не мешает слайдам",
      len(run([cam[k % 2] for k in range(12)] +
              [img((8, 8, 120, 16), (8, 24, 90, 56), (104, 24 + k % 2 * 8, 128, 80)) for k in range(8)])), 2)
check("частая смена — не чаще раза в 3 с", len(run([A] * 3 + [B] * 3 + [A] * 3 + [B] * 3)), 2)

# ---------- видео: слайды с полосой камер, камера во весь экран, снова слайды ----------
tmp = Path(tempfile.mkdtemp(prefix="pod-test-"))
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
SLIDES = [("Итоги квартала", ["Выручка выросла на 12%", "Три новых клиента", "Отток снизился", "План на ноябрь"]),
          ("Риски проекта", ["Сдвиг сроков поставки", "Нет второго разработчика", "Бюджет на тесты"]),
          ("Следующие шаги", ["Созвон с подрядчиком 12.11", "Черновик договора", "Демо заказчику", "Отчёт"])]
call = tmp / "2026-10-01_10-00-00"  # старше 7 дней — пригодится для проверки срока
if Path(CHROME).exists():
    pngs = []
    for i, (title, items) in enumerate(SLIDES, 1):
        h = tmp / f"s{i}.html"
        h.write_text(f"""<html><body style="margin:0;width:1280px;height:800px;font-family:Arial">
<div style="height:90px;background:#1f3b73;color:#fff;font-size:44px;padding:20px 40px">{title}</div>
<ul style="font-size:34px;line-height:1.7;margin:40px 60px">{''.join(f'<li>{x}</li>' for x in items)}</ul>
</body></html>""", encoding="utf-8")
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--window-size=1280,800",
                        f"--screenshot={tmp}/s{i}.png", h.as_uri()], capture_output=True, timeout=60)
        pngs.append(tmp / f"s{i}.png")
    strip = "mandelbrot=s=240x800:rate=25:end_scale=0.05,trim=duration={d}"
    fc = []
    for i, png in enumerate(pngs):
        fc.append(f"[{i}]scale=1280:800,fps=25,trim=duration=8,setpts=PTS-STARTPTS[p{i}];"
                  f"{strip.format(d=8)},setpts=PTS-STARTPTS[m{i}];[p{i}][m{i}]overlay=x=W-w,format=yuv420p[s{i}]")
    fc.append("mandelbrot=s=1280x800:rate=25:end_scale=0.01,trim=duration=6,format=yuv420p[cam]")
    fc.append("[s0][cam][s1][s2]concat=n=4:v=1:a=0[o]")
    vid = tmp / "deck.mp4"
    args = sum([["-loop", "1", "-i", str(p)] for p in pngs], [])
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", *args, "-filter_complex", ";".join(fc),
                    "-map", "[o]", "-c:v", "libx264", "-preset", "ultrafast", str(vid)], check=True)
    subprocess.run([PY, str(LIB / "frames.py"), "scan", str(vid), str(call)], check=True, capture_output=True)
    items = json.loads((call / "frames.json").read_text())
    ts = [it["t"] for it in items]
    print("   кадры на секундах:", ts)
    check("видео: три слайда → три кадра", len(ts), 3)
    check("видео: в «камере» (8–14 с) кадров нет", [t for t in ts if 8 <= t < 14], [])
    lag = [round(min(t - s for s in (0, 14, 22) if t >= s), 1) for t in ts]
    print("   задержка после смены слайда, с:", lag)
    check("видео: кадр не позже 5 с после смены слайда", all(x <= 5 for x in lag), True)
else:
    print("     (нет Chrome — видео-проверка пропущена, кадры возьму заглушками)")
    (call / "frames").mkdir(parents=True)
    items = []
    for t in (1.5, 15.8):
        f = f"frames/{F.stamp(t)}.jpg"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x800:d=1",
                        "-frames:v", "1", str(call / f)], check=True)
        items.append({"t": t, "file": f})
    (call / "frames.json").write_text(json.dumps(items))

# ---------- вставка в протокол, «Память», срок хранения ----------
(call / "transcript.srt").write_text("1\n00:00:01,000 --> 00:00:04,000\nОни: Смотрим первый слайд.\n\n"
                                     "2\n00:00:15,000 --> 00:00:18,000\nЯ: Здесь цифры за квартал.\n", encoding="utf-8")
(call / "transcript.md").write_text("# Звонок\n\n## Расшифровка\n\n- `00:00:01` **Они:** Смотрим первый слайд.\n"
                                    "- `00:00:15` **Я:** Здесь цифры за квартал.\n", encoding="utf-8")
(call / "protocol.md").write_text("# Протокол\n\n## Кратко\n\nТест.\n", encoding="utf-8")
for _ in range(2):  # повторный attach не дублирует раздел
    subprocess.run([PY, str(LIB / "frames.py"), "attach", str(call)], check=True, capture_output=True)
proto = (call / "protocol.md").read_text()
check("протокол: раздел «Слайды» один", proto.count("## Слайды"), 1)
check("протокол: все кадры вставлены", proto.count("![Кадр "), len(items))
check("протокол: под кадром реплика", "> Они: Смотрим первый слайд." in proto, True)
tr = (call / "transcript.md").read_text()
check("расшифровка: кадры между репликами", tr.count("![Кадр "), len(items))
inbox = subprocess.run([PY, str(LIB / "frames.py"), "inbox", str(call / "protocol.md")],
                       capture_output=True, text=True).stdout
check("«Память»: без картинок", "![" in inbox, False)
check("«Память»: сказано, сколько кадров", f"Кадров экрана: {len(items)}" in inbox, True)

pdf = subprocess.run([PY, str(LIB / "topdf.py"), str(call / "protocol.md"), str(call / "protocol.pdf")],
                     capture_output=True, text=True)
check("PDF с кадрами собран", pdf.returncode == 0 and (call / "protocol.pdf").stat().st_size > 50_000, True)

home = tmp / "home"
(home / ".Trash").mkdir(parents=True)
fresh = tmp / time.strftime("%Y-%m-%d_%H-%M-%S")
(fresh / "frames").mkdir(parents=True)
env = dict(os.environ, HOME=str(home))
subprocess.run([PY, str(LIB / "frames.py"), "clean", str(tmp), "7"], check=True, env=env, capture_output=True)
check("срок: старые кадры ушли в Корзину", (call / "frames").exists(), False)
check("срок: в Корзине лежат", any((home / ".Trash").iterdir()), True)
check("срок: свежие кадры на месте", (fresh / "frames").is_dir(), True)
proto = (call / "protocol.md").read_text()
check("срок: картинки вырезаны из протокола", "![" in proto, False)
check("срок: PDF пересобран без картинок", (call / "protocol.pdf").stat().st_size < 50_000, True)

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

print("\nвсё прошло" if ok else "\nЕСТЬ ПАДЕНИЯ")
print("рабочая папка:", tmp)
sys.exit(0 if ok else 1)
