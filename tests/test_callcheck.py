"""Сторож звука звонка: раннее предупреждение и флаг nocall (П-5).

  python3 -m unittest tests/test_callcheck.py      около 25 с: К6 идёт в реальном времени
"""
import array
import importlib.util
import math
import subprocess
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "lib" / "callcheck.py"
spec = importlib.util.spec_from_file_location("callcheck", LIB)
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)

RATE = 8000


def make_wav(path: Path, seconds: int, mic_amp: int, call_amp: int, ch: int = 3) -> None:
    """Каналы как у Podslushka In: микрофон, затем два канала звонка."""
    a = array.array("h")
    for i in range(seconds * RATE):
        s = math.sin(2 * math.pi * 440 * i / RATE)
        frame = [int(mic_amp * s)] * (ch - 2) + [int(call_amp * s)] * 2
        a.extend(frame)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(a.tobytes())


class Watch:
    """Запускает live в потоке, ловит уведомления и момент появления флага."""

    def __init__(self, wav: Path, life: float):
        self.dir = wav.parent
        self.proc = subprocess.Popen(["sleep", str(life)])
        self.pid = self.dir / "ffmpeg.pid"
        self.pid.write_text(str(self.proc.pid))
        self.flag = self.dir / "nocall"
        self.notes = []
        self.flag_at = None
        cc.notify = lambda text, sound=False: self.notes.append((round(time.time() - self.t0, 1), text, sound))
        self.t0 = time.time()
        self.th = threading.Thread(target=cc.live, args=(wav, self.pid), daemon=True)
        self.th.start()

    def run(self, until: float) -> None:
        while time.time() - self.t0 < until and self.th.is_alive():
            if self.flag_at is None and self.flag.exists():
                self.flag_at = time.time() - self.t0
            time.sleep(0.2)

    def stop(self) -> None:
        self.proc.kill()
        self.proc.wait()
        self.th.join(timeout=10)


class LiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (cc.POLL, cc.EARLY_WARN, cc.WARN_AFTER)

    def tearDown(self):
        cc.POLL, cc.EARLY_WARN, cc.WARN_AFTER = self.saved

    def test_k6_voice_without_call_warns_in_15_to_20_s(self):
        wav = self.tmp / "audio.wav"
        make_wav(wav, 6, mic_amp=8000, call_amp=0)
        w = Watch(wav, 40)
        w.run(22)
        self.assertIsNotNone(w.flag_at, "флаг nocall не появился за 22 с")
        self.assertGreaterEqual(w.flag_at, 15)
        self.assertLessEqual(w.flag_at, 20.5)
        self.assertEqual(len(w.notes), 1)
        self.assertTrue(w.notes[0][2], "предупреждение должно быть со звуком")
        w.stop()
        self.assertFalse(w.flag.exists(), "флаг должен сниматься, когда запись кончилась")

    def test_k7_total_silence_no_early_warning_but_late_one(self):
        cc.POLL, cc.EARLY_WARN, cc.WARN_AFTER = 1, 2, 6
        wav = self.tmp / "audio.wav"
        make_wav(wav, 6, mic_amp=0, call_amp=0)
        w = Watch(wav, 20)
        w.run(4)
        self.assertIsNone(w.flag_at, "при общей тишине раннего предупреждения быть не должно")
        w.run(10)
        self.assertIsNotNone(w.flag_at, "позднее предупреждение не пришло")
        self.assertGreaterEqual(w.flag_at, 6)
        w.stop()

    def test_room_noise_is_not_voice(self):
        cc.POLL, cc.EARLY_WARN, cc.WARN_AFTER = 1, 2, 30
        wav = self.tmp / "audio.wav"
        make_wav(wav, 6, mic_amp=200, call_amp=0)   # шум ниже MIC_VOICE
        w = Watch(wav, 8)
        w.run(6)
        self.assertIsNone(w.flag_at)
        w.stop()

    def test_call_present_no_warning(self):
        cc.POLL, cc.EARLY_WARN, cc.WARN_AFTER = 1, 2, 3
        wav = self.tmp / "audio.wav"
        make_wav(wav, 6, mic_amp=8000, call_amp=3000)
        w = Watch(wav, 10)
        w.run(6)
        self.assertFalse(w.th.is_alive(), "при звуке звонка сторож должен сразу уйти")
        self.assertIsNone(w.flag_at)
        self.assertEqual(w.notes, [])
        w.stop()


class SummaryTest(unittest.TestCase):
    def test_summary_counts_call_seconds(self):
        tmp = Path(tempfile.mkdtemp())
        wav = tmp / "audio.wav"
        make_wav(wav, 3, mic_amp=8000, call_amp=0)
        self.assertEqual(cc.summary(wav), 3)
        make_wav(wav, 3, mic_amp=0, call_amp=3000)
        self.assertEqual(cc.summary(wav), 0)
        self.assertIn('"call_audio_seconds": 3', (tmp / "meta.json").read_text())


if __name__ == "__main__":
    unittest.main()
