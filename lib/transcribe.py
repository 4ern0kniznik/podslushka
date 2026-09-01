#!/usr/bin/env python3
"""Транскрибация локальным faster-whisper. Ничего не уходит в сеть.

  ~/.whisper-venv/bin/python transcribe.py audio.wav [ещё файлы...]

Рядом с аудио кладёт transcript.txt, transcript.srt, transcript.md.
Переменные окружения: PODSLUSHKA_MODEL (medium), PODSLUSHKA_LANG (авто),
PODSLUSHKA_COMPUTE (int8).
"""
import json
import os
import sys
import time
from pathlib import Path

from faster_whisper import WhisperModel

MODEL = os.environ.get("PODSLUSHKA_MODEL", "medium")
LANG = os.environ.get("PODSLUSHKA_LANG") or None
COMPUTE = os.environ.get("PODSLUSHKA_COMPUTE", "int8")


def ts(seconds: float, comma: bool = False) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    sep = "," if comma else "."
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def run(audio: Path, model: WhisperModel) -> None:
    started = time.time()
    segments, info = model.transcribe(
        str(audio),
        language=LANG,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 700},
        beam_size=5,
        condition_on_previous_text=False,
    )
    print(f"  язык: {info.language} ({info.language_probability:.2f}), "
          f"длительность: {info.duration/60:.1f} мин", flush=True)

    out = audio.parent
    lines, srt, md = [], [], []
    for i, seg in enumerate(segments, 1):
        text = seg.text.strip()
        if not text:
            continue
        lines.append(text)
        srt.append(f"{i}\n{ts(seg.start, True)} --> {ts(seg.end, True)}\n{text}\n")
        md.append(f"- `{ts(seg.start)[:8]}` {text}")
        if i % 25 == 0:
            print(f"  … {ts(seg.end)[:8]}", flush=True)

    (out / "transcript.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "transcript.srt").write_text("\n".join(srt), encoding="utf-8")
    meta_path = out / "meta.json"
    meta = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    meta.update({"duration": round(info.duration, 2), "lang": info.language,
                 "model": MODEL, "transcribed_at": time.strftime("%Y-%m-%d %H:%M")})
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    (out / "transcript.md").write_text(
        f"# Звонок {out.name}\n\n"
        f"Файл: `{audio.name}` · язык: {info.language} · "
        f"{info.duration/60:.1f} мин\n\n## Расшифровка\n\n" + "\n".join(md) + "\n",
        encoding="utf-8",
    )
    print(f"  готово за {time.time()-started:.0f} c → {out}", flush=True)


def main() -> int:
    files = [Path(a).expanduser() for a in sys.argv[1:]]
    if not files:
        print(__doc__)
        return 2
    missing = [f for f in files if not f.is_file()]
    if missing:
        print("нет файлов: " + ", ".join(map(str, missing)))
        return 1

    print(f"модель {MODEL} ({COMPUTE}) — первый запуск может качать веса…", flush=True)
    model = WhisperModel(MODEL, device="cpu", compute_type=COMPUTE)
    for f in files:
        print(f"→ {f}", flush=True)
        run(f, model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
