#!/usr/bin/env python3
"""Подслушка: локальный плеер и картотека записей. Только стандартная библиотека.

  python3 server.py [--port 8477] [--dir ~/Documents/Calls]

Слушает только 127.0.0.1. Наружу ничего не отдаёт.
"""
import argparse
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = Path(os.path.expanduser(
    os.environ.get("PODSLUSHKA_DIR", "~/Documents/Подслушка")))
HOME_DIR = Path(os.path.expanduser(
    os.environ.get("PODSLUSHKA_HOME", "~/.podslushka")))
WHISPER_PY = HOME_DIR / "venv" / "bin" / "python"
AUDIO_NAMES = ("audio.m4a", "audio.wav", "audio.mp3", "audio.flac")
JOBS = {}          # call_id -> {"state": ..., "log": [...]}
JOBS_LOCK = threading.Lock()

SRT_TIME = re.compile(r"(\d\d):(\d\d):(\d\d)[,.](\d\d\d)")


# ---------- модель данных ----------

def call_dirs():
    if not BASE.is_dir():
        return []
    out = [d for d in BASE.iterdir()
           if d.is_dir() and not d.name.startswith((".", "_"))]
    return sorted(out, key=lambda d: d.name, reverse=True)


def find_audio(d: Path):
    for n in AUDIO_NAMES:
        p = d / n
        if p.is_file():
            return p
    for p in sorted(d.iterdir()):
        if p.suffix.lower() in (".m4a", ".wav", ".mp3", ".flac", ".ogg"):
            return p
    return None


def read_meta(d: Path) -> dict:
    p = d / "meta.json"
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def write_meta(d: Path, meta: dict) -> None:
    (d / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_srt(path: Path):
    if not path.is_file():
        return []
    segs, cur = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "-->" in line:
            times = SRT_TIME.findall(line)
            if len(times) == 2:
                cur = {"start": srt_secs(times[0]), "end": srt_secs(times[1]), "text": ""}
        elif not line:
            if cur and cur["text"]:
                segs.append(cur)
            cur = None
        elif cur is not None:
            cur["text"] = (cur["text"] + " " + line).strip()
        # строка с номером просто игнорируется
    if cur and cur["text"]:
        segs.append(cur)
    return segs


def srt_secs(t) -> float:
    h, m, s, ms = (int(x) for x in t)
    return h * 3600 + m * 60 + s + ms / 1000


def fmt_ts(sec: float, comma=False) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{',' if comma else '.'}{ms:03d}"


def parse_stamp(name: str):
    try:
        return datetime.strptime(name[:19], "%Y-%m-%d_%H-%M-%S")
    except ValueError:
        return None


def call_summary(d: Path) -> dict:
    meta = read_meta(d)
    audio = find_audio(d)
    segs = parse_srt(d / "transcript.srt")
    when = parse_stamp(d.name)
    dur = meta.get("duration") or (segs[-1]["end"] if segs else 0)
    with JOBS_LOCK:
        job = JOBS.get(d.name, {}).get("state")
    return {
        "id": d.name,
        "title": meta.get("title") or "",
        "when": when.isoformat(sep=" ", timespec="minutes") if when else d.name,
        "duration": dur,
        "size": audio.stat().st_size if audio else 0,
        "audio": audio.name if audio else None,
        "segments": len(segs),
        "lang": meta.get("lang", ""),
        "model": meta.get("model", ""),
        "tags": meta.get("tags", []),
        "note": meta.get("note", ""),
        "job": job,
    }


def write_transcripts(d: Path, segs) -> None:
    (d / "transcript.txt").write_text(
        "\n".join(s["text"] for s in segs) + "\n", encoding="utf-8")
    (d / "transcript.srt").write_text("\n".join(
        f"{i}\n{fmt_ts(s['start'], True)} --> {fmt_ts(s['end'], True)}\n{s['text']}\n"
        for i, s in enumerate(segs, 1)), encoding="utf-8")
    meta = read_meta(d)
    title = meta.get("title") or d.name
    (d / "transcript.md").write_text(
        f"# {title}\n\n## Расшифровка\n\n" + "\n".join(
            f"- `{fmt_ts(s['start'])[:8]}` {s['text']}" for s in segs) + "\n",
        encoding="utf-8")


# ---------- фоновая расшифровка ----------

def retranscribe(call_id: str, model: str) -> None:
    d = BASE / call_id
    audio = find_audio(d)
    if not audio:
        return
    env = dict(os.environ, PODSLUSHKA_MODEL=model)
    with JOBS_LOCK:
        JOBS[call_id] = {"state": f"расшифровка ({model})", "log": []}
    try:
        proc = subprocess.run(
            [str(WHISPER_PY), str(HERE / "transcribe.py"), str(audio)],
            env=env, capture_output=True, text=True, timeout=60 * 180)
        ok = proc.returncode == 0
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-8:]
        if ok:
            meta = read_meta(d)
            meta["model"] = model
            write_meta(d, meta)
        with JOBS_LOCK:
            JOBS[call_id] = {"state": "готово" if ok else "ошибка", "log": tail}
    except Exception as exc:  # noqa: BLE001
        with JOBS_LOCK:
            JOBS[call_id] = {"state": "ошибка", "log": [str(exc)]}
    finally:
        threading.Timer(90, lambda: JOBS.pop(call_id, None)).start()


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "Podslushka/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    # -- helpers --
    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def safe_call(self, call_id: str):
        d = (BASE / call_id).resolve()
        if d.parent != BASE.resolve() or not d.is_dir():
            return None
        return d

    # -- GET --
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path, qs = url.path, urllib.parse.parse_qs(url.query)
        try:
            if path == "/":
                return self.send_file(HERE / "ui.html", "text/html; charset=utf-8")
            if path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            if path == "/api/calls":
                return self.send_json([call_summary(d) for d in call_dirs()])
            if path == "/api/search":
                return self.send_json(self.do_search(qs.get("q", [""])[0]))
            if path.startswith("/api/call/"):
                return self.api_call_get(path.split("/")[3])
            if path.startswith("/audio/"):
                return self.serve_audio(urllib.parse.unquote(path[len("/audio/"):]))
            self.send_error(404)
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001
            self.send_json({"error": str(exc)}, 500)

    def api_call_get(self, call_id):
        d = self.safe_call(call_id)
        if not d:
            return self.send_error(404)
        peaks = []
        pf = d / "peaks.json"
        if pf.is_file():
            try:
                peaks = json.loads(pf.read_text())
            except Exception:
                peaks = []
        info = call_summary(d)
        info["peaks"] = peaks
        info["segs"] = parse_srt(d / "transcript.srt")
        return self.send_json(info)

    def do_search(self, q):
        try:
            q = q.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        q = q.strip().lower()
        if len(q) < 2:
            return []
        hits = []
        for d in call_dirs():
            for s in parse_srt(d / "transcript.srt"):
                if q in s["text"].lower():
                    hits.append({"id": d.name, "title": read_meta(d).get("title", ""),
                                 "start": s["start"], "text": s["text"]})
                    if len(hits) >= 200:
                        return hits
        return hits

    def send_file(self, p: Path, ctype):
        if not p.is_file():
            return self.send_error(404)
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def serve_audio(self, rel):
        parts = rel.split("/")
        if len(parts) != 2:
            return self.send_error(404)
        d = self.safe_call(parts[0])
        if not d:
            return self.send_error(404)
        p = (d / parts[1]).resolve()
        if p.parent != d.resolve() or not p.is_file():
            return self.send_error(404)

        size = p.stat().st_size
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        code = 200
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            if a:
                start = int(a)
                end = int(b) if b else size - 1
            elif b:                       # bytes=-N — последние N байт
                start = max(0, size - int(b))
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            code = 206

        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with p.open("rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(262144, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    do_HEAD = do_GET

    # -- POST --
    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        parts = url.path.strip("/").split("/")
        try:
            if len(parts) != 4 or parts[0] != "api" or parts[1] != "call":
                return self.send_error(404)
            call_id, action = parts[2], parts[3]
            d = self.safe_call(call_id)
            if not d:
                return self.send_error(404)
            data = self.body_json()

            if action == "meta":
                meta = read_meta(d)
                for k in ("title", "note", "tags"):
                    if k in data:
                        meta[k] = data[k]
                write_meta(d, meta)
                return self.send_json(call_summary(d))

            if action == "transcript":
                segs = [{"start": float(s["start"]), "end": float(s["end"]),
                         "text": str(s["text"]).strip()} for s in data.get("segs", [])
                        if str(s.get("text", "")).strip()]
                write_transcripts(d, segs)
                return self.send_json({"ok": True, "segments": len(segs)})

            if action == "retranscribe":
                model = str(data.get("model", "medium"))
                if model not in ("tiny", "base", "small", "medium", "large-v3"):
                    return self.send_json({"error": "неизвестная модель"}, 400)
                with JOBS_LOCK:
                    if call_id in JOBS and JOBS[call_id]["state"].startswith("расшифровка"):
                        return self.send_json({"error": "уже идёт"}, 409)
                threading.Thread(target=retranscribe, args=(call_id, model),
                                 daemon=True).start()
                return self.send_json({"ok": True})

            if action == "delete":
                trash = BASE / "_deleted"
                trash.mkdir(exist_ok=True)
                dest = trash / d.name
                if dest.exists():
                    dest = trash / f"{d.name}-{int(time.time())}"
                shutil.move(str(d), str(dest))
                return self.send_json({"ok": True, "moved_to": str(dest)})

            if action == "reveal":
                subprocess.Popen(["open", str(d)])
                return self.send_json({"ok": True})

            self.send_error(404)
        except Exception as exc:  # noqa: BLE001
            self.send_json({"error": str(exc)}, 500)


def main():
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8477)
    ap.add_argument("--dir", default=str(BASE))
    args = ap.parse_args()
    BASE = Path(os.path.expanduser(args.dir)).resolve()
    BASE.mkdir(parents=True, exist_ok=True)

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Подслушка: http://127.0.0.1:{args.port}  (папка {BASE})")
    print("Ctrl-C чтобы остановить")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nостановлено")


if __name__ == "__main__":
    main()
