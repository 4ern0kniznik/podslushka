#!/bin/bash
# Подслушка — установка. Запускать из папки репозитория:  ./install.sh
set -uo pipefail

HOME_DIR="${PODSLUSHKA_HOME:-$HOME/.podslushka}"
REC_DIR="${PODSLUSHKA_DIR:-$HOME/Documents/Подслушка}"
SRC="$(cd "$(dirname "$0")" && pwd)"
MODEL="${PODSLUSHKA_MODEL:-medium}"

say()  { printf '\n\033[1m== %s\033[0m\n' "$1"; }
ok()   { printf '   %s\n' "$1"; }
die()  { printf '\n\033[31mОстановка: %s\033[0m\n' "$1"; exit 1; }

[ "$(uname)" = "Darwin" ] || die "это только для macOS"

say "Проверка окружения"
command -v brew >/dev/null || die "нет Homebrew. Поставь его с https://brew.sh и запусти снова"
ok "homebrew есть"

PY="$(command -v python3 || true)"
[ -n "$PY" ] || die "нет python3. Поставь: brew install python"
PYV="$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
"$PY" -c 'import sys;raise SystemExit(0 if sys.version_info>=(3,9) else 1)' \
  || die "нужен python 3.9 или новее, найден $PYV"
ok "python $PYV"

say "ffmpeg"
if command -v ffmpeg >/dev/null; then ok "уже есть"; else brew install ffmpeg || die "brew install ffmpeg не прошёл"; fi

say "BlackHole 2ch — виртуальная звуковая карта"
if [ -d /Library/Audio/Plug-Ins/HAL/BlackHole2ch.driver ]; then
  ok "уже есть"
  BH_FRESH=0
else
  echo "   Ставится драйвер, macOS попросит пароль."
  brew install --cask blackhole-2ch || die "не удалось поставить blackhole-2ch"
  BH_FRESH=1
fi

say "Окружение python в $HOME_DIR/venv"
mkdir -p "$HOME_DIR"
if [ -x "$HOME_DIR/venv/bin/python" ]; then
  ok "уже есть"
else
  "$PY" -m venv "$HOME_DIR/venv" || die "не создалось виртуальное окружение"
fi
"$HOME_DIR/venv/bin/pip" install --quiet --upgrade pip || true
"$HOME_DIR/venv/bin/pip" install --quiet faster-whisper || die "не поставился faster-whisper"
ok "faster-whisper $("$HOME_DIR/venv/bin/python" -c 'import faster_whisper;print(faster_whisper.__version__)')"

say "Файлы"
mkdir -p "$HOME_DIR/bin" "$HOME_DIR/lib" "$HOME_DIR/app" "$REC_DIR"
cp -f "$SRC"/bin/* "$HOME_DIR/bin/"
cp -f "$SRC"/lib/* "$HOME_DIR/lib/"
cp -f "$SRC"/app/Podslushka.swift "$SRC"/app/build.sh "$SRC"/app/icon.png "$HOME_DIR/app/" 2>/dev/null
chmod +x "$HOME_DIR"/bin/* "$HOME_DIR/app/build.sh"
ok "разложены в $HOME_DIR"
ok "записи будут в $REC_DIR"

say "Приложение"
if command -v swiftc >/dev/null; then
  "$HOME_DIR/app/build.sh" >/dev/null && ok "собрано: ~/Applications/Podslushka.app" \
    || echo "   собрать не вышло, интерфейс будет недоступен — команды всё равно работают"
else
  echo "   нет swiftc, приложение пропущено."
  echo "   Поставь инструменты разработчика (xcode-select --install) и запусти $HOME_DIR/app/build.sh"
fi

say "Команды в PATH"
LINKDIR=""
for d in /usr/local/bin /opt/homebrew/bin; do
  [ -d "$d" ] && [ -w "$d" ] && LINKDIR="$d" && break
done
if [ -n "$LINKDIR" ]; then
  ln -sf "$HOME_DIR/bin/podslushka" "$LINKDIR/podslushka"
  ln -sf "$HOME_DIR/bin/podslushka-ui" "$LINKDIR/podslushka-ui"
  ln -sf "$HOME_DIR/bin/podslushka-devices" "$LINKDIR/podslushka-devices"
  ok "ссылки в $LINKDIR"
else
  echo "   не нашёл, куда положить ссылки. Добавь в ~/.zshrc:"
  echo "     export PATH=\"\$PATH:$HOME_DIR/bin\""
fi

say "Модель распознавания ($MODEL, около 1,5 ГБ)"
CACHE="$HOME/.cache/huggingface/hub/models--Systran--faster-whisper-$MODEL"
if [ -d "$CACHE" ]; then
  ok "уже скачана"
  ans=n
else
  printf '   Скачать сейчас? [Y/n] '
  read -r ans
fi
case "${ans:-y}" in
  [Nn]*) [ -d "$CACHE" ] || echo "   пропущено — скачается сама при первой расшифровке" ;;
  *) "$HOME_DIR/venv/bin/python" - <<PY || echo "   не скачалось, попробуется при первой записи"
from faster_whisper import WhisperModel
WhisperModel("$MODEL", device="cpu", compute_type="int8")
print("   модель на месте")
PY
  ;;
esac

say "Готово"
if [ "${BH_FRESH:-0}" = 1 ]; then
  echo "   Драйвер только что поставлен. Выполни, чтобы система его увидела:"
  echo "     sudo killall coreaudiod"
  echo
fi
cat <<'NEXT'
   Дальше:
     1. open ~/Applications/Podslushka.app     панель управления
     2. в её меню «Создать аудиоустройства»    соберёт Podslushka In и Out
     3. podslushka doctor                      проверка, что всё на месте

   Записать без интерфейса:  podslushka        (второй раз — стоп)
   Посмотреть записи:        podslushka-ui
NEXT
