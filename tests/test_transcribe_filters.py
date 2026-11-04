#!/usr/bin/env python3
"""Фильтр выдумок whisper: python3 tests/test_transcribe_filters.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import transcribe as T  # noqa: E402

ok = True


def check(name, got, want):
    global ok
    print(("ok   " if got == want else "FAIL ") + name)
    ok &= got == want


check("призрак: Продолжение следует...", T.is_ghost("Продолжение следует..."), True)
check("призрак: Субтитры сделал DimaTorzok", T.is_ghost(" Субтитры сделал DimaTorzok"), True)
check("призрак: Спасибо за просмотр!", T.is_ghost("Спасибо за просмотр!"), True)
check("речь: Всем спасибо, до связи", T.is_ghost("Всем спасибо, до связи."), False)
check("речь: продолжение следует в середине", T.is_ghost("Обсудим, продолжение следует завтра"), False)
check("whisper: тишина и неуверенность — выбросить",
      T.keep({"text": "Ага", "no_speech_prob": 0.9, "avg_logprob": -1.5}), False)
check("whisper: тишина, но уверенно — оставить",
      T.keep({"text": "Ага", "no_speech_prob": 0.9, "avg_logprob": -0.3}), True)
check("окно MLX короче 30 с", T.WINDOW < 30, True)
print("всё прошло" if ok else "есть провалы")
sys.exit(0 if ok else 1)
