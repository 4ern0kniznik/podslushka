#!/bin/bash
# Подслушка — удаление. Записи не трогает.
set -uo pipefail
HOME_DIR="${PODSLUSHKA_HOME:-$HOME/.podslushka}"

pkill -x Podslushka 2>/dev/null
rm -rf "$HOME/Applications/Podslushka.app"
rm -rf "$HOME_DIR"
for d in /usr/local/bin /opt/homebrew/bin; do
  rm -f "$d/podslushka" "$d/podslushka-ui" "$d/podslushka-devices" 2>/dev/null
done
defaults delete local.podslushka.app 2>/dev/null

echo "Удалено. Что осталось намеренно:"
echo "  записи в ~/Documents/Подслушка"
echo "  ffmpeg и BlackHole — общие инструменты, сноси вручную, если не нужны:"
echo "    brew uninstall ffmpeg"
echo "    brew uninstall --cask blackhole-2ch"
echo "  устройства Podslushka In и Podslushka Out — удали в «Настройка Audio-MIDI»"
