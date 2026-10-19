#!/bin/bash
# Разбор отчёта приложения в podslushka doctor (П-5). Звук мака не трогает:
# отчёты заготовлены строками.  bash tests/test_route_verdict.sh
cd "$(dirname "$0")/.." || exit 1
P=bin/podslushka
T=$'\t'
fails=0
check() {   # имя, ожидаемый код, подстрока в выводе, отчёт, идёт ли запись
  local out code
  out="$(PODSLUSHKA_DIR="$(mktemp -d)" bash "$P" route-verdict "$4" "$5" 2>&1)"; code=$?
  if [ "$code" = "$2" ] && printf '%s' "$out" | grep -qF -- "$3"; then
    echo "ok    $1"
  else
    echo "FAIL  $1 (код $code, ждали $2; нет «$3»)"; printf '%s\n' "$out" | sed 's/^/      /'
    fails=$((fails + 1))
  fi
}

OK_HP="output${T}AirPods Pro${T}headphones
autoswitch${T}0
setting_out${T}AirPods Pro
setting_mic${T}Микрофон MacBook Pro${T}builtin
podout_subs${T}AirPods Pro, BlackHole 2ch
podout${T}ok"

check "наушники, всё исправно" 0 "выход сам переключится на Podslushka Out" "$OK_HP" 0
check "К8: Podslushka Out собран на другом выходе" 1 "НЕТ    Podslushka Out: собран на" \
  "${OK_HP/podout${T}ok/podout${T}problem${T}собран на «Динамики», а звук в «AirPods Pro»}" 0
check "К8: запись идёт мимо Podslushka Out" 1 "мимо Podslushka Out — звонка в записи не будет" "$OK_HP" 1
check "запись идёт через Podslushka Out" 0 "системный выход — Podslushka Out" \
  "${OK_HP/AirPods Pro${T}headphones/Podslushka Out${T}podslushka-out}" 1
check "К8: Bluetooth-микрофон — совет" 0 "совет  микрофон «AirPods Pro» — Bluetooth" \
  "${OK_HP/Микрофон MacBook Pro${T}builtin/AirPods Pro${T}bluetooth}" 0
check "микрофона нет" 1 "микрофона «X» сейчас нет" \
  "${OK_HP/Микрофон MacBook Pro${T}builtin/X${T}missing}" 0
check "К8: приложение мимо, запись не идёт — только сведения" 0 "Zoom → AirPods Pro" \
  "$OK_HP
bypass${T}Zoom → AirPods Pro" 0
check "К8: приложение мимо во время записи — сбой" 1 "НЕТ    играют мимо" \
  "${OK_HP/AirPods Pro${T}headphones/Podslushka Out${T}podslushka-out}
bypass${T}Zoom → AirPods Pro" 1
check "пустой отчёт" 1 "не ответило" "" 0

# К9: старое приложение без метки не запускается, doctor говорит «обнови».
OLD="$(mktemp -d)/Podslushka"
printf '#!/bin/bash\ntouch "%s.ran"\n' "$OLD" > "$OLD"; chmod +x "$OLD"
out="$(PODSLUSHKA_APP="$OLD" PODSLUSHKA_DIR="$(mktemp -d)" bash "$P" doctor 2>&1)"
if printf '%s' "$out" | grep -qF "приложение старое" && [ ! -e "$OLD.ran" ]; then
  echo "ok    К9: старое приложение не запущено"
else
  echo "FAIL  К9: старое приложение"; fails=$((fails + 1))
fi

[ "$fails" = 0 ] && echo "всё прошло" || echo "упало: $fails"
exit "$fails"
