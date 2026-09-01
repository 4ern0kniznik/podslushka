#!/bin/bash
# Сборка Подслушка.app из одного файла на Swift. Нужны Command Line Tools.
set -e
cd "$(dirname "$0")"

APP="$HOME/Applications/Podslushka.app"
BIN="$APP/Contents/MacOS/Podslushka"

command -v swiftc >/dev/null || {
  echo "нет swiftc. Поставь инструменты разработчика:  xcode-select --install"
  exit 1
}

echo "== сборка приложения =="
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# Файл содержит код верхнего уровня, поэтому компилятору он подсовывается
# под именем main.swift — иначе swiftc его не примет.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cp Podslushka.swift "$TMP/main.swift"
swiftc -O "$TMP/main.swift" -o "$BIN" -framework AppKit -framework CoreAudio

if [ -f icon.png ]; then
  SET="$TMP/AppIcon.iconset"; mkdir -p "$SET"
  for sz in 16 32 128 256 512; do
    sips -z $sz $sz icon.png --out "$SET/icon_${sz}x${sz}.png" >/dev/null
    sips -z $((sz*2)) $((sz*2)) icon.png --out "$SET/icon_${sz}x${sz}@2x.png" >/dev/null
  done
  iconutil -c icns "$SET" -o "$APP/Contents/Resources/AppIcon.icns"
fi

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Podslushka</string>
  <key>CFBundleDisplayName</key><string>Подслушка</string>
  <key>CFBundleIdentifier</key><string>local.podslushka.app</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleExecutable</key><string>Podslushka</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key>
  <string>Запись звонка: микрофон и звук системы через устройство Podslushka In.</string>
  <key>NSAudioCaptureUsageDescription</key>
  <string>Запись звука звонка.</string>
</dict>
</plist>
PLIST

# Подпись «для себя»: без неё macOS теряет выданное разрешение на микрофон
# при каждой пересборке и спрашивает заново.
codesign --force --deep --sign - "$APP" 2>/dev/null || echo "  подписать не вышло, не страшно"
touch "$APP"

echo "готово: $APP"
