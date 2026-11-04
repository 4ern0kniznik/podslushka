#!/usr/bin/env python3
"""Автокадры по смене слайда. Своя реализация, ничего не уходит в сеть.

  frames.py watch <папка звонка> <pid-файл>  снимать экран, пока идёт запись
  frames.py scan <видео> <папка звонка>       то же по видеофайлу (проверка, лекции)
  frames.py attach <папка звонка>             кадры в protocol.md и transcript.md
  frames.py inbox <protocol.md>               протокол без кадров — для «Памяти»
  frames.py clean <папка записей> [дней]      кадры старше срока (7 дней) в Корзину

Как ловится слайд. Каждые 0,75 с снимок главного экрана сжимается до 128×80
в серых тонах и делится на 16×10 клеток. Клетки, которые меняются раз за
разом, — видео собеседников, курсор, анимация — сторож исключает сам.
Кадр сохраняется, когда остальной экран простоял 1,5 с и с прошлого кадра
в нём сменилось не меньше 6 клеток: смена одного текста на слайде того же
шаблона даёт 20 с лишним. Кадр приходит через 2–5 с после смены: столько
нужно, чтобы отличить слайд от видео. Видео во весь экран кадров не даёт;
прокрутка документа — даёт, когда прокрутка встала.

Кадры — это экран, на нём бывают переписка, пароли и персданные. Поэтому
они лежат только в папке звонка, в «Память» уходит протокол без них, а через
7 дней папка frames/ уходит в Корзину и картинки вырезаются из протокола.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

W, H = 128, 80   # подпись снимка: серая картинка 128×80
GW, GH = 16, 10  # сетка клеток 8×8 точек подписи
PIX = 12         # точка сменилась, если яркость ушла больше чем на 12 из 255
CELL = 0.08      # клетка сменилась, если в ней сменилось больше 8% точек
TICK = 0.75      # с между снимками
RUN = 2          # тиков подряд без смены (2 × 0,75 = 1,5 с) — картинка встала
NEW = 6          # клеток, сменившихся с прошлого кадра, — уже новый слайд
LIVE = 3          # клетка сменилась 3 раза за 6 тиков (4,5 с) — там видео или курсор
MEMORY = 6
MIN_STATIC = 0.3  # меньше 30% экрана неподвижно — это видео, кадров не надо
GAP = 5.0         # с, не чаще одного кадра (слайд, что стоит дольше, всё равно попадёт)
# Правый верхний угол — баннеры уведомлений macOS: 4 клетки × 2 ряда в расчёт не идут.
BANNER = {gy * GW + gx for gy in range(2) for gx in range(GW - 4, GW)}
MAX_FRAMES = int(os.environ.get("PODSLUSHKA_FRAMES_MAX", "300"))
WIDTH = 1920    # кадр ужимается до этой ширины
BEGIN, END = "<!-- кадры: начало -->", "<!-- кадры: конец -->"
LIB = Path(__file__).resolve().parent


def changed(a: bytes, b: bytes) -> list[bool]:
    """Какие клетки сетки сменились между двумя подписями."""
    cw, ch = W // GW, H // GH
    need = CELL * cw * ch
    out = []
    for gy in range(GH):
        for gx in range(GW):
            n = 0
            for y in range(gy * ch, (gy + 1) * ch):
                r = y * W + gx * cw
                n += sum(1 for x, z in zip(a[r:r + cw], b[r:r + cw]) if abs(x - z) > PIX)
            out.append(n > need and gy * GW + gx not in BANNER)
    return out


class Detector:
    """Решает по подписям снимков, сохранять ли текущий. Без ввода-вывода.

    Клетки, которые меняются раз за разом (видео собеседников, курсор,
    анимация), считаются живыми и в расчёт не идут. Остальные — слайд:
    он встал, если в них ничего не сменилось 1,5 с, и он новый, если
    с прошлого кадра в них сменилось не меньше NEW клеток.
    """

    def __init__(self):
        self.prev = None
        self.saved = None
        self.saved_t = -1e9
        self.run = 0
        self.history: list[list[bool]] = []

    def feed(self, t: float, sig: bytes) -> bool:
        if self.prev is None:
            self.prev = sig
            return False
        ch = changed(sig, self.prev)
        self.prev = sig
        self.history = (self.history + [ch])[-MEMORY:]
        live = [sum(h[i] for h in self.history) >= LIVE for i in range(GW * GH)]
        moving = sum(1 for c, l in zip(ch, live) if c and not l)
        self.run = self.run + 1 if moving <= 1 else 0  # одна клетка — курсор мыши
        if self.run < RUN or t - self.saved_t < GAP:
            return False
        if live.count(False) < MIN_STATIC * GW * GH:
            return False
        if self.saved is not None:
            new = sum(1 for c, l in zip(changed(sig, self.saved), live) if c and not l)
            if new < NEW:
                return False
        self.saved, self.saved_t = sig, t
        return True


def signature(img: Path) -> bytes | None:
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(img),
                        "-vf", f"scale={W}:{H},format=gray", "-f", "rawvideo", "-"],
                       capture_output=True, timeout=20)
    return r.stdout if len(r.stdout) == W * H else None


def stamp(t: float) -> str:
    t = int(t)
    return f"{t // 3600:02d}-{t % 3600 // 60:02d}-{t % 60:02d}"


class Store:
    """Папка frames/ и список frames.json в папке звонка."""

    def __init__(self, call: Path):
        self.dir = call / "frames"
        self.index = call / "frames.json"
        self.items = []
        if self.index.is_file():
            try:
                self.items = json.loads(self.index.read_text(encoding="utf-8"))
            except Exception:
                self.items = []

    def save(self, src: Path, t: float) -> bool:
        if len(self.items) >= MAX_FRAMES:
            return False
        self.dir.mkdir(exist_ok=True)
        name = f"{stamp(t)}.jpg"
        dst = self.dir / name
        r = subprocess.run(["sips", "-Z", str(WIDTH), "-s", "format", "jpeg",
                            "-s", "formatOptions", "70", str(src), "--out", str(dst)],
                           capture_output=True)
        if r.returncode != 0 or not dst.is_file():
            shutil.copyfile(src, dst)
        self.items.append({"t": round(t, 1), "file": f"frames/{name}"})
        tmp = self.index.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.items, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.index)
        return True


def started_at(call: Path) -> float:
    """Начало записи — из имени папки ГГГГ-ММ-ДД_ЧЧ-ММ-СС."""
    try:
        return datetime.strptime(call.name[:19], "%Y-%m-%d_%H-%M-%S").timestamp()
    except ValueError:
        return time.time()


def alive(pid_file: Path) -> bool:
    try:
        os.kill(int(pid_file.read_text().strip()), 0)
        return True
    except Exception:
        return False


def watch(call: Path, pid_file: Path) -> int:
    t0, det, store, fails = started_at(call), Detector(), Store(call), 0
    print(f"  кадры: слежу за экраном → {call / 'frames'}", flush=True)
    with tempfile.TemporaryDirectory(prefix="podslushka-frames-") as tmp:
        shot = Path(tmp) / "shot.jpg"
        while alive(pid_file):
            tick = time.time()
            r = subprocess.run(["screencapture", "-x", "-t", "jpg", str(shot)],
                               capture_output=True)
            sig = signature(shot) if r.returncode == 0 and shot.is_file() else None
            if sig is None:
                fails += 1
                if fails >= 5:
                    print("  кадры: экран не снимается (нет разрешения «Запись экрана» "
                          "для Подслушки?) — кадров не будет", flush=True)
                    return 1
            else:
                fails = 0
                if det.feed(tick, sig) and store.save(shot, tick - t0):
                    print(f"  кадр {stamp(tick - t0)}", flush=True)
            time.sleep(max(0.0, TICK - (time.time() - tick)))
    print(f"  кадры: запись кончилась, кадров {len(store.items)}", flush=True)
    return 0


def scan(video: Path, call: Path) -> int:
    """Тот же детектор по видеофайлу: кадры раз в TICK секунд."""
    call.mkdir(parents=True, exist_ok=True)
    det, store = Detector(), Store(call)
    with tempfile.TemporaryDirectory(prefix="podslushka-scan-") as tmp:
        subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(video),
                        "-vf", f"fps=1/{TICK}", "-q:v", "3", f"{tmp}/%06d.jpg"], check=True)
        for i, f in enumerate(sorted(Path(tmp).glob("*.jpg"))):
            t = i * TICK
            sig = signature(f)
            if sig and det.feed(t, sig):
                store.save(f, t)
    print(f"кадров: {len(store.items)} → {store.dir}")
    for it in store.items:
        print(f"  {it['t']:7.1f} с  {it['file']}")
    return 0


# ---------- вставка в протокол и расшифровку ----------

def phrases(call: Path) -> list[tuple[float, str]]:
    srt = call / "transcript.srt"
    if not srt.is_file():
        return []
    out = []
    for block in srt.read_text(encoding="utf-8").strip().split("\n\n"):
        lines = block.splitlines()
        if len(lines) >= 3:
            m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", lines[1])
            if m:
                h, mi, s, ms = map(int, m.groups())
                out.append((h * 3600 + mi * 60 + s + ms / 1000, " ".join(lines[2:])))
    return out


def near(ph: list[tuple[float, str]], t: float) -> str:
    """Реплика, которая звучала, когда появился кадр (или первая после)."""
    best = ""
    for start, text in ph:
        if start <= t + 1:
            best = text
        else:
            return best or text
    return best


def mmss(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60:02d}:{t % 60:02d}"


def cut(md: str) -> str:
    return re.sub(rf"\n*{re.escape(BEGIN)}.*?{re.escape(END)}\n*", "\n", md, flags=re.S).rstrip() + "\n"


def attach(call: Path) -> int:
    items = Store(call).items
    items = [it for it in items if (call / it["file"]).is_file()]
    if not items:
        return 0
    ph = phrases(call)
    block = [BEGIN, "## Слайды", "",
             f"Кадры экрана при смене картинки: {len(items)}. Хранятся 7 дней, потом "
             f"уходят в Корзину.", ""]
    for it in items:
        say = near(ph, it["t"])
        block += [f"### {mmss(it['t'])}", "", f"![Кадр {mmss(it['t'])}]({it['file']})", ""]
        if say:
            block += [f"> {say[:300]}", ""]
    block.append(END)
    proto = call / "protocol.md"
    if proto.is_file():
        proto.write_text(cut(proto.read_text(encoding="utf-8")) + "\n" + "\n".join(block) + "\n",
                         encoding="utf-8")
    tr = call / "transcript.md"
    if tr.is_file():
        lines = [l for l in tr.read_text(encoding="utf-8").splitlines()
                 if not l.startswith("![Кадр ")]
        out, k = [], 0
        for l in lines:
            m = re.match(r"- `(\d+):(\d+):(\d+)`", l)
            if m:
                t = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
                while k < len(items) and items[k]["t"] <= t + 1:
                    out += [f"![Кадр {mmss(items[k]['t'])}]({items[k]['file']})", ""]
                    k += 1
            out.append(l)
        out += [f"![Кадр {mmss(it['t'])}]({it['file']})" for it in items[k:]]
        tr.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"  кадров в протоколе: {len(items)}")
    return 0


def inbox(proto: Path) -> int:
    """Протокол для «Памяти»: без картинок, только сколько их было."""
    md = proto.read_text(encoding="utf-8")
    n = len(re.findall(r"^!\[Кадр ", md, flags=re.M))
    md = cut(md)
    if n:
        md += f"\n_Кадров экрана: {n} — в папке звонка, хранятся 7 дней._\n"
    sys.stdout.write(md)
    return 0


# ---------- срок хранения ----------

def trash(path: Path, label: str) -> None:
    bin_ = Path.home() / ".Trash"
    dst = bin_ / label
    i = 1
    while dst.exists():
        i += 1
        dst = bin_ / f"{label} {i}"
    shutil.move(str(path), str(dst))


def clean(base: Path, days: float) -> int:
    limit = time.time() - days * 86400
    n = 0
    for call in sorted(p for p in base.iterdir() if p.is_dir()):
        fr = call / "frames"
        if not fr.is_dir() or started_at(call) > limit:
            continue
        trash(fr, f"Подслушка кадры {call.name}")
        (call / "frames.json").unlink(missing_ok=True)
        gone = f"\n_Кадры экрана удалены {time.strftime('%d.%m.%Y')} по сроку хранения ({days:g} дн.)._\n"
        for name in ("protocol.md", "transcript.md"):
            f = call / name
            if f.is_file():
                md = f.read_text(encoding="utf-8")
                md = cut(md) if name == "protocol.md" else \
                    "\n".join(l for l in md.splitlines() if not l.startswith("![Кадр ")) + "\n"
                f.write_text(md + gone, encoding="utf-8")
        # В PDF картинки вшиты — пересобираем без них.
        if (call / "protocol.pdf").is_file() and (call / "protocol.md").is_file():
            subprocess.run([sys.executable, str(LIB / "topdf.py"), str(call / "protocol.md"),
                            str(call / "protocol.pdf")], capture_output=True)
        n += 1
        print(f"  кадры {call.name} → Корзина")
    return 0


def main() -> int:
    a = sys.argv[1:]
    cmd = a[0] if a else ""
    if cmd == "watch" and len(a) == 3:
        return watch(Path(a[1]), Path(a[2]))
    if cmd == "scan" and len(a) == 3:
        return scan(Path(a[1]), Path(a[2]))
    if cmd == "attach" and len(a) == 2:
        return attach(Path(a[1]).expanduser())
    if cmd == "inbox" and len(a) == 2:
        return inbox(Path(a[1]).expanduser())
    if cmd == "clean" and len(a) in (2, 3):
        return clean(Path(a[1]).expanduser(), float(a[2]) if len(a) == 3 else 7)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
