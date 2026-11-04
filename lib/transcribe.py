#!/usr/bin/env python3
"""Транскрибация локальной моделью whisper. Ничего не уходит в сеть.

  ~/.podslushka/venv/bin/python transcribe.py audio.wav [ещё файлы...]

Рядом с аудио кладёт transcript.txt, transcript.srt, transcript.md.

Движок: на Apple Silicon с пакетом mlx-whisper — MLX на видеоядре, иначе
faster-whisper на процессоре. Замер 04.11.2026 (tests/asr_bench): large-v3-turbo
на MLX точен как large-v3 и в 25 раз быстрее его на CPU.

Переменные окружения: PODSLUSHKA_MODEL (large-v3-turbo), PODSLUSHKA_ENGINE
(auto | mlx | fw), PODSLUSHKA_LANG (авто), PODSLUSHKA_COMPUTE (int8, только fw).
"""
import json
import os
import re
import sys
import time
from pathlib import Path

MODEL = os.environ.get("PODSLUSHKA_MODEL", "large-v3-turbo")
LANG = os.environ.get("PODSLUSHKA_LANG") or None
COMPUTE = os.environ.get("PODSLUSHKA_COMPUTE", "int8")
ENGINE = os.environ.get("PODSLUSHKA_ENGINE", "auto")
WINDOW = 25  # с, окно для MLX: меньше 30 с, чтобы whisper не резал его сам

# Короткие имена моделей → веса MLX на Hugging Face (качаются один раз в кэш).
MLX_REPOS = {
    "medium": "mlx-community/whisper-medium-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "turbo": "mlx-community/whisper-large-v3-turbo",
}


def pick_engine() -> str:
    if ENGINE in ("mlx", "fw"):
        return ENGINE
    try:
        import mlx_whisper  # noqa: F401
        return "mlx"
    except Exception:
        return "fw"


class Seg:
    def __init__(self, start: float, end: float, text: str):
        self.start, self.end, self.text = start, end, text


# Фразы, которые whisper дописывает на шуме и тишине: выучил на субтитрах
# к роликам. В разговоре они не звучат, а в протоколе путают модель.
GHOSTS = re.compile(
    r"^(продолжение следует|субтитры (сделал|создавал|подготовил)|редактор субтитров|"
    r"корректор|спасибо за просмотр|подписывайтесь на канал|thanks for watching)",
    re.I)


def is_ghost(text: str) -> bool:
    return bool(GHOSTS.match(text.strip(" .!…-")))


def keep(x: dict) -> bool:
    """Сегмент MLX — речь, а не выдумка на шуме: правило самого whisper
    (высокая вероятность тишины при низкой уверенности) плюс фразы-призраки."""
    if x.get("no_speech_prob", 0) > 0.6 and x.get("avg_logprob", 0) < -1.0:
        return False
    return not is_ghost(x["text"])


class Info:
    def __init__(self, language: str, prob: float, duration: float):
        self.language, self.language_probability, self.duration = language, prob, duration


def speech_spans(audio: Path):
    """Где в записи речь — тем же Silero VAD, что у faster-whisper.

    У MLX своего VAD нет, а whisper на долгой тишине сочиняет фразы
    («Продолжение следует…»). Куски без речи в MLX не отдаём.
    """
    try:
        from faster_whisper.audio import decode_audio
        from faster_whisper.vad import VadOptions, get_speech_timestamps
    except Exception:
        return None
    wav = decode_audio(str(audio), sampling_rate=16000)
    ts = get_speech_timestamps(wav, VadOptions(min_silence_duration_ms=500, speech_pad_ms=300,
                                               max_speech_duration_s=WINDOW))
    return [(t["start"] / 16000, t["end"] / 16000) for t in ts], wav


def transcribe_mlx(audio: Path):
    import mlx_whisper
    import numpy as np

    repo = MLX_REPOS.get(MODEL, MODEL)
    found = speech_spans(audio)
    kw = dict(path_or_hf_repo=repo, language=LANG, condition_on_previous_text=False,
              verbose=None)
    if found is None:  # без faster-whisper — целиком, как есть
        r = mlx_whisper.transcribe(str(audio), **kw)
        segs = [Seg(x["start"], x["end"], x["text"]) for x in r["segments"] if keep(x)]
        dur = segs[-1].end if segs else 0.0
        return segs, Info(r.get("language") or "?", 1.0, dur)

    spans, wav = found
    dur = len(wav) / 16000
    # Куски речи склеиваются в окна не длиннее WINDOW секунд, и каждое окно
    # whisper разбирает за один заход. На длинных окнах turbo в MLX бывает
    # отдаёт за первые 30 с одну выдуманную фразу и теряет реплики (запись
    # 04.11 с паузой в начале). Время сегментов пересчитывается в шкалу записи.
    windows, cur = [], None
    for a, b in spans:
        if cur and b - cur[0] <= WINDOW:
            cur[1] = b
        else:
            if cur:
                windows.append(cur)
            cur = [a, b]
    if cur:
        windows.append(cur)

    segs, lang = [], LANG
    for a, b in windows:
        piece = wav[int(a * 16000):int(b * 16000)].astype(np.float32)
        r = mlx_whisper.transcribe(piece, **{**kw, "language": lang})
        lang = lang or r.get("language")
        for x in r["segments"]:
            if keep(x):
                segs.append(Seg(a + x["start"], a + x["end"], x["text"]))
    return segs, Info(lang or "?", 1.0, dur)


def ts(seconds: float, comma: bool = False) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    sep = "," if comma else "."
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def run(audio: Path, model) -> None:
    started = time.time()
    if model is None:
        segments, info = transcribe_mlx(audio)
    else:
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
        if not text or is_ghost(text):
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
                 "model": MODEL, "engine": "mlx" if model is None else "faster-whisper",
                 "transcribed_at": time.strftime("%Y-%m-%d %H:%M")})
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

    if pick_engine() == "mlx":
        print(f"модель {MODEL} (MLX) — первый запуск может качать веса…", flush=True)
        model = None
    else:
        from faster_whisper import WhisperModel
        print(f"модель {MODEL} ({COMPUTE}) — первый запуск может качать веса…", flush=True)
        model = WhisperModel(MODEL, device="cpu", compute_type=COMPUTE)
    for f in files:
        print(f"→ {f}", flush=True)
        run(f, model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
