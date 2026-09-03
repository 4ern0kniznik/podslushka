#!/usr/bin/env python3
"""Markdown → PDF без сторонних библиотек.

  python3 topdf.py protocol.md [out.pdf]

Разбирает подмножество markdown, которое мы сами и порождаем: заголовки,
списки, таблицы, выделение, цитаты, разделители. Верстает через безголовый
Chrome; если его нет — через встроенный в macOS cupsfilter.
"""
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CHROMES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/opt/pw-browsers/chromium",
    shutil.which("chromium") or "",
    shutil.which("google-chrome") or "",
]

CSS = """
@page { size: A4; margin: 18mm 16mm 20mm; }
* { box-sizing: border-box; }
body {
  margin: 0; color: #16150f; background: #fff;
  font: 11pt/1.55 -apple-system, "Helvetica Neue", "PT Sans", Arial, sans-serif;
}
h1 { font-size: 20pt; margin: 0 0 2mm; line-height: 1.2; }
h2 { font-size: 13pt; margin: 9mm 0 2mm; padding-bottom: 1.5mm;
     border-bottom: 0.6pt solid #d8d5cc; page-break-after: avoid; }
h3 { font-size: 11.5pt; margin: 6mm 0 1.5mm; page-break-after: avoid; }
p, li { orphans: 3; widows: 3; }
p { margin: 0 0 2.5mm; }
ul, ol { margin: 0 0 3mm; padding-left: 6mm; }
li { margin-bottom: 1mm; }
blockquote { margin: 0 0 3mm; padding: 1mm 0 1mm 4mm;
             border-left: 2pt solid #c2410c; color: #4a483f; }
hr { border: 0; border-top: 0.6pt solid #d8d5cc; margin: 6mm 0; }
code { font: 9.5pt/1.4 ui-monospace, Menlo, monospace; background: #f2f0ea;
       padding: 0.3mm 1mm; border-radius: 1mm; }
table { width: 100%; border-collapse: collapse; margin: 0 0 4mm;
        font-size: 10pt; page-break-inside: auto; }
th, td { border: 0.6pt solid #d8d5cc; padding: 1.6mm 2.2mm;
         text-align: left; vertical-align: top; }
th { background: #f7f5f0; font-weight: 600; }
tr { page-break-inside: avoid; }
.meta { color: #6b6960; font-size: 9.5pt; margin: 0 0 7mm;
        padding-bottom: 3mm; border-bottom: 1.2pt solid #16150f; }
.foot { margin-top: 10mm; padding-top: 3mm; border-top: 0.6pt solid #d8d5cc;
        color: #8b8880; font-size: 8.5pt; }
"""


def inline(t: str) -> str:
    t = html.escape(t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", t)
    return t


def md_to_html(md: str) -> str:
    out, lines, i = [], md.splitlines(), 0
    list_tag = None

    def close_list():
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None

    while i < len(lines):
        line = lines[i].rstrip()

        # таблица: строка с | и следующая из дефисов
        if (line.startswith("|") and i + 1 < len(lines)
                and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip())):
            close_list()
            cells = [c.strip() for c in line.strip("|").split("|")]
            out.append("<table><thead><tr>"
                       + "".join(f"<th>{inline(c)}</th>" for c in cells)
                       + "</tr></thead><tbody>")
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>")
                i += 1
            out.append("</tbody></table>")
            continue

        if not line.strip():
            close_list()
            i += 1
            continue

        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            close_list()
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>")
            i += 1
            continue

        if re.match(r"^([-*_])\1{2,}$", line.strip()):
            close_list()
            out.append("<hr>")
            i += 1
            continue

        if line.startswith("> "):
            close_list()
            out.append(f"<blockquote>{inline(line[2:])}</blockquote>")
            i += 1
            continue

        m = re.match(r"^\s*[-*]\s+(.*)$", line)
        if m:
            if list_tag != "ul":
                close_list()
                out.append("<ul>")
                list_tag = "ul"
            out.append(f"<li>{inline(m.group(1))}</li>")
            i += 1
            continue

        m = re.match(r"^\s*\d+[.)]\s+(.*)$", line)
        if m:
            if list_tag != "ol":
                close_list()
                out.append("<ol>")
                list_tag = "ol"
            out.append(f"<li>{inline(m.group(1))}</li>")
            i += 1
            continue

        # Абзац: соседние обычные строки склеиваются в один <p>, иначе перенос
        # в исходнике превращается в отдельный абзац с отбивкой.
        close_list()
        para = [line.strip()]
        i += 1
        while i < len(lines):
            nxt = lines[i].rstrip()
            if (not nxt.strip() or nxt.startswith(("#", ">", "|"))
                    or re.match(r"^\s*([-*]\s+|\d+[.)]\s+)", nxt)
                    or re.match(r"^([-*_])\1{2,}$", nxt.strip())):
                break
            para.append(nxt.strip())
            i += 1
        out.append(f"<p>{inline(' '.join(para))}</p>")

    close_list()
    return "\n".join(out)


def find_chrome() -> str | None:
    for c in CHROMES:
        if c and Path(c).exists():
            return c
    return None


def to_pdf(html_path: Path, pdf_path: Path) -> bool:
    chrome = find_chrome()
    if chrome:
        cmd = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
               "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}",
               html_path.as_uri()]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if pdf_path.is_file() and pdf_path.stat().st_size > 1000:
            return True
        print("   Chrome не сверстал:", (r.stderr or "").strip()[:300])
    cups = shutil.which("cupsfilter")
    if cups:
        with pdf_path.open("wb") as f:
            r = subprocess.run([cups, str(html_path)], stdout=f,
                               stderr=subprocess.DEVNULL, timeout=120)
        if pdf_path.is_file() and pdf_path.stat().st_size > 1000:
            return True
    return False


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = Path(sys.argv[1]).expanduser()
    if not src.is_file():
        print(f"нет файла: {src}")
        return 1
    pdf = Path(sys.argv[2]).expanduser() if len(sys.argv) > 2 else src.with_suffix(".pdf")

    md = src.read_text(encoding="utf-8")
    title = next((l.lstrip("# ").strip() for l in md.splitlines()
                  if l.startswith("# ")), src.stem)
    body = md_to_html(md)
    page = (f"<!doctype html><html lang=ru><head><meta charset=utf-8>"
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head>"
            f"<body>{body}"
            f"<div class=foot>Составлено автоматически из записи разговора. "
            f"Расшифровка может содержать ошибки распознавания — "
            f"сверяйтесь с аудио в спорных местах.</div></body></html>")

    with tempfile.TemporaryDirectory() as tmp:
        hp = Path(tmp) / "protocol.html"
        hp.write_text(page, encoding="utf-8")
        if not to_pdf(hp, pdf):
            fallback = src.with_suffix(".html")
            fallback.write_text(page, encoding="utf-8")
            print(f"PDF не собрался. HTML рядом: {fallback}")
            print("Открой его и напечатай в PDF из браузера.")
            return 1
    size = pdf.stat().st_size
    print(f"   PDF: {pdf} ({size // 1024} КБ)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
