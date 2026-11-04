#!/usr/bin/env python3
"""Сравнение моделей распознавания на одной синтетической записи.

  python3 tests/asr_bench/bench.py [папка для результатов]

Запись своя: диалог из ref.txt читает голос Milena (say), дальше телефонная
полоса 300–3400 Гц, 8 кГц и розовый шум — похоже на звонок. Эталон известен
дословно, поэтому WER честный. Личные записи из ~/Подслушка не нужны.

Каждая модель идёт в отдельном процессе под /usr/bin/time -l: время работы
без загрузки и пиковая память процесса. Первый прогон модели греет кэш и
качает веса, в таблицу идёт второй.

Окружения: faster-whisper — ~/.podslushka/venv; mlx-whisper —
$BENCH_MLX_PY (venv с пакетом mlx-whisper). Нет окружения — строка пропускается.
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FW_PY = os.path.expanduser(os.environ.get("BENCH_FW_PY", "~/.podslushka/venv/bin/python"))
MLX_PY = os.path.expanduser(os.environ.get("BENCH_MLX_PY", ""))

FW_ONE = """
import sys, time
from faster_whisper import WhisperModel
name, audio, out = sys.argv[1:4]
m = WhisperModel(name, device="cpu", compute_type="int8")
t = time.time()
segs, info = m.transcribe(audio, language="ru", vad_filter=True,
    vad_parameters={"min_silence_duration_ms": 700}, beam_size=5,
    condition_on_previous_text=False)
txt = " ".join(s.text.strip() for s in segs)
print(f"RUN {time.time()-t:.1f}")
open(out, "w").write(txt)
"""

MLX_ONE = """
import sys, time, mlx_whisper
repo, audio, out = sys.argv[1:4]
t = time.time()
r = mlx_whisper.transcribe(audio, path_or_hf_repo=repo, language="ru",
                           condition_on_previous_text=False)
print(f"RUN {time.time()-t:.1f}")
open(out, "w").write(r["text"].strip())
"""

MODELS = [  # (подпись, движок, имя)
    ("whisper medium · faster-whisper CPU int8", "fw", "medium"),
    ("whisper large-v3 · faster-whisper CPU int8", "fw", "large-v3"),
    ("whisper large-v3-turbo · faster-whisper CPU int8", "fw", "large-v3-turbo"),
    ("whisper medium · MLX GPU", "mlx", "mlx-community/whisper-medium-mlx"),
    ("whisper large-v3 · MLX GPU", "mlx", "mlx-community/whisper-large-v3-mlx"),
    ("whisper large-v3-turbo · MLX GPU", "mlx", "mlx-community/whisper-large-v3-turbo"),
]


def words(s: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", s.lower().replace("ё", "е")).split()


def wer(ref: list[str], hyp: list[str]) -> float:
    d = list(range(len(hyp) + 1))
    for i, a in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, b in enumerate(hyp, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (a != b))
    return d[-1] / len(ref)


def make_audio(work: Path) -> Path:
    lines = [l for l in (HERE / "ref.txt").read_text(encoding="utf-8").splitlines() if l.strip()]
    parts = []
    for i, line in enumerate(lines, 1):
        p = work / f"p{i}.aiff"
        # два «собеседника» — один голос на разной скорости
        subprocess.run(["say", "-v", "Milena", "-r", "175" if i % 2 else "195", "-o", str(p), line],
                       stdin=subprocess.DEVNULL, check=True, timeout=60)
        parts.append(f"file '{p}'")
    (work / "list.txt").write_text("\n".join(parts) + "\n")
    clean, test = work / "clean.wav", work / "test.m4a"
    ff = ["ffmpeg", "-v", "error", "-y", "-nostdin"]
    subprocess.run(ff + ["-f", "concat", "-safe", "0", "-i", str(work / "list.txt"),
                         "-ar", "48000", "-ac", "1", str(clean)], check=True)
    subprocess.run(ff + ["-i", str(clean), "-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.02:r=48000",
                         "-filter_complex", "[0]highpass=f=300,lowpass=f=3400,aresample=8000,aresample=48000[v];"
                         "[v][1]amix=inputs=2:duration=first:weights=1 1[o]",
                         "-map", "[o]", "-ac", "1", "-c:a", "aac", "-b:a", "64k", str(test)], check=True)
    return test


def run_one(py: str, code: str, name: str, audio: Path, out: Path) -> tuple[float, float] | None:
    script = out.with_suffix(".py")
    script.write_text(code)
    subprocess.run([py, str(script), name, str(audio), "/dev/null"], capture_output=True)  # прогрев
    r = subprocess.run(["/usr/bin/time", "-l", py, str(script), name, str(audio), str(out)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("   не вышло:", r.stderr.strip().splitlines()[-1:] or "?")
        return None
    run = float(re.search(r"RUN ([\d.]+)", r.stdout).group(1))
    rss = int(re.search(r"(\d+)\s+maximum resident set size", r.stderr).group(1)) / 2**30
    return run, rss


def main() -> int:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="asr-bench-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    audio = make_audio(out_dir)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", str(audio)], capture_output=True, text=True).stdout)
    ref = words((HERE / "ref.txt").read_text(encoding="utf-8"))
    print(f"запись {dur:.0f} с, {len(ref)} слов → {out_dir}\n")
    print("| Модель | WER | Время на запись | × реального | Память, ГБ |")
    print("|---|---|---|---|---|")
    for label, eng, name in MODELS:
        py, code = (FW_PY, FW_ONE) if eng == "fw" else (MLX_PY, MLX_ONE)
        if not py or not os.path.exists(py):
            print(f"| {label} | — | нет окружения | — | — |")
            continue
        out = out_dir / (re.sub(r"\W+", "_", f"{eng}_{name}") + ".txt")
        res = run_one(py, code, name, audio, out)
        if res is None:
            print(f"| {label} | — | ошибка | — | — |")
            continue
        run, rss = res
        w = wer(ref, words(out.read_text()))
        print(f"| {label} | {w*100:.1f}% | {run:.1f} с | {dur/run:.0f}× | {rss:.1f} |", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
