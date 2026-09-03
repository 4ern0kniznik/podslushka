#!/usr/bin/env python3
"""Протокол встречи из расшифровки.

  python3 protocol.py <папка звонка> [--engine ollama|prompt] [--model имя]

Два пути:

  ollama — локальная модель, всё офлайн. Разбирает расшифровку кусками,
           потом сводит в протокол. Пишет protocol.md.
  prompt — заготовка для внешней модели: protocol-prompt.md с инструкцией
           и расшифровкой. Отдаёшь её Claude, ответ кладёшь в protocol.md.

Без указания движка берётся ollama, если он установлен, иначе prompt.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

CHUNK = 7000          # знаков расшифровки за один заход к модели
TIMEOUT = 60 * 20     # локальная модель на длинном звонке думает долго

ЗАДАНИЕ = """Ты составляешь протокол рабочей встречи из автоматической расшифровки.

Правила:
- Пиши по-русски, деловым языком, без воды и без вводных оборотов.
- Опирайся только на текст расшифровки. Ничего не додумывай.
- Расшифровка сделана машиной: имена и термины могут быть искажены. Если имя
  звучит неуверенно, пиши «участник» вместо выдуманного имени.
- Если раздела в разговоре не было, напиши «не обсуждалось» и не выдумывай.
- Сроки переноси так, как они прозвучали.
"""

СТРУКТУРА = """Верни markdown ровно такой структуры, без пояснений до и после:

## Кратко

Три-четыре предложения: о чём был разговор и чем закончился.

## Обсуждение

Связный текст по темам, каждая тема — подзаголовок третьего уровня.

## Решения

Маркированный список. Только то, о чём договорились, а не то, что предлагали.

## Поручения

Таблица с колонками: Что | Кто | Срок. Если исполнитель или срок не назван —
поставь прочерк. Если поручений не было, напиши «Поручений не зафиксировано».

## Открытые вопросы

Маркированный список того, что осталось нерешённым.
"""


def ollama_models() -> list[str]:
    if not shutil.which("ollama"):
        return []
    try:
        r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=20)
    except Exception:
        return []
    if r.returncode != 0:
        return []
    out = []
    for line in r.stdout.splitlines()[1:]:
        name = line.split()[0] if line.split() else ""
        if name:
            out.append(name)
    return out


def ask(model: str, prompt: str) -> str | None:
    try:
        r = subprocess.run(["ollama", "run", model], input=prompt,
                           capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        print("   модель думала слишком долго")
        return None
    if r.returncode != 0:
        print("   ollama:", (r.stderr or "").strip()[:300])
        return None
    return r.stdout.strip()


def split_text(t: str, size: int = CHUNK) -> list[str]:
    words, parts, cur = t.split(), [], []
    n = 0
    for w in words:
        cur.append(w)
        n += len(w) + 1
        if n >= size:
            parts.append(" ".join(cur))
            cur, n = [], 0
    if cur:
        parts.append(" ".join(cur))
    return parts or [t]


def header(meta: dict, folder: Path) -> str:
    stamp = folder.name[:19]
    try:
        when = datetime.strptime(stamp, "%Y-%m-%d_%H-%M-%S")
        when_s = when.strftime("%d.%m.%Y, %H:%M")
    except ValueError:
        when_s = folder.name
    dur = meta.get("duration") or 0
    mins = f"{dur/60:.0f} мин" if dur else "длительность неизвестна"
    title = meta.get("title") or "Протокол встречи"
    return f"# {title}\n\n**Дата:** {when_s} · **Длительность:** {mins}\n"


def build_with_ollama(model: str, text: str) -> str | None:
    parts = split_text(text)
    facts = []
    for n, part in enumerate(parts, 1):
        print(f"   разбор куска {n} из {len(parts)}…", flush=True)
        ans = ask(model, f"""{ЗАДАНИЕ}
Это фрагмент {n} из {len(parts)}. Выпиши из него только факты: темы, договорённости,
поручения с исполнителями и сроками, нерешённые вопросы. Списком, без вступления.

Фрагмент:
{part}
""")
        if ans is None:
            return None
        facts.append(ans)

    print("   свожу протокол…", flush=True)
    return ask(model, f"""{ЗАДАНИЕ}
Ниже выписки из всех фрагментов разговора. Сведи их в один протокол,
убрав повторы и противоречия.

{СТРУКТУРА}

Выписки:
{chr(10).join(facts)}
""")


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    folder = Path(args[0]).expanduser()
    if not folder.is_dir():
        print(f"нет папки: {folder}")
        return 1

    engine = None
    model = os.environ.get("PODSLUSHKA_LLM")
    if "--engine" in args:
        engine = args[args.index("--engine") + 1]
    if "--model" in args:
        model = args[args.index("--model") + 1]

    src = folder / "transcript.txt"
    if not src.is_file():
        print("нет transcript.txt — сначала расшифровка")
        return 1
    text = src.read_text(encoding="utf-8").strip()
    if len(text) < 200:
        print("расшифровка слишком короткая для протокола")
        return 1

    meta = {}
    mp = folder / "meta.json"
    if mp.is_file():
        try:
            meta = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            pass

    models = ollama_models()
    if engine is None:
        engine = "ollama" if models else "prompt"

    if engine == "ollama":
        if not models:
            print("ollama не установлен или моделей нет. Поставь:")
            print("  brew install ollama && ollama serve &")
            print("  ollama pull qwen2.5:7b")
            engine = "prompt"
        else:
            use = model if model in models else models[0]
            print(f"   локальная модель: {use}")
            body = build_with_ollama(use, text)
            if body:
                out = folder / "protocol.md"
                out.write_text(header(meta, folder) + "\n" + body.strip() + "\n",
                               encoding="utf-8")
                print(f"   протокол: {out}")
                return 0
            print("   локальная модель не справилась, готовлю заготовку")
            engine = "prompt"

    out = folder / "protocol-prompt.md"
    out.write_text(f"""{ЗАДАНИЕ}
{СТРУКТУРА}
Начни ответ прямо с «## Кратко».

Шапку протокола ставить не нужно, она добавится сама:

{header(meta, folder)}

Расшифровка разговора:

{text}
""", encoding="utf-8")
    print(f"   заготовка: {out}")
    print("   Отдай её модели, ответ сохрани в protocol.md рядом,")
    print("   затем собери PDF:  podslushka pdf " + str(folder))
    return 0


if __name__ == "__main__":
    sys.exit(main())
