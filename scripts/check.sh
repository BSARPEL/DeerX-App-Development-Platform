#!/usr/bin/env sh
# Yerel denetim: lint + testler. Deponun TEK dogrulamasi.
#
# GitHub Actions is akisi 2026-09-02'de kaldirildi: denetim bu makinede
# kalir, GitHub'a hicbir sey yansimaz. Once alti ortam kosuluyordu
# (Ubuntu/Windows/macOS x Python 3.11/3.13); artik kosulan ne varsa
# burada kosuyor.
#
#   ./scripts/check.sh            lint + tum suit (bu Python)
#   ./scripts/check.sh --fast     lint + surec baslatmayan testler
#   ./scripts/check.sh --pythons  ustune 3.11 ve 3.13'te de suit
#
# `--pythons` kaybedilen seyin YARISINI geri getirir: surum farklari.
# Isletim sistemi farkini bir makine veremez, ve o fark gercek --
# Windows'a ozgu bir cikis kodu bekleyen tek bir test, Linux ve macOS'ta
# on bes kosu boyunca kirmizi kaldi, bu makinede hep yesil gorundu.
set -e

cd "$(dirname "$0")/.."

# CI'nin kosturdugu surumler. Ana ortam bunlardan biri olabilir; yine de
# ayri kosulur, cunku ana ortamda BUTUN ekler kurulu ve eksik bir ekle
# bozulan bir sey orada gorunmez.
SURUMLER="3.11 3.13"

HIZLI=0
COKLU=0
for arg in "$@"; do
  case "$arg" in
    --fast)    HIZLI=1 ;;
    --pythons) COKLU=1 ;;
    *) printf 'Bilinmeyen secenek: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

printf '== ruff check src tests\n'
uv run --no-sync ruff check src tests

if [ "$HIZLI" -eq 1 ]; then
  printf '\n== pytest -q (hizli: gercek surec baslatan testler haric)\n'
  uv run --no-sync pytest -q -m "not slow"
else
  printf '\n== pytest -q\n'
  uv run --no-sync pytest -q
fi

if [ "$COKLU" -eq 1 ]; then
  for surum in $SURUMLER; do
    # Yan ortam: ana `.venv` hic dokunulmadan kalir. `--extra dev`, eski
    # CI ile ayni -- gomme ve tarayici ekleri olmadan da kosmali.
    ortam=".venv-check-$surum"
    printf '\n== Python %s (yan ortam: %s)\n' "$surum" "$ortam"

    # Yan ortam SESSIZCE bozulabiliyor. OLCULDU: `uv`nin konsol betigi
    # kalintisi `uv trampoline failed to canonicalize script path`
    # veriyor, `python.exe` calisiyor ama `pytest.exe` calismiyor -- ve
    # `uv sync` BUNU ONARMIYOR. Denetim tek test kosmadan duser, mesaj
    # da neyin bozuldugunu soylemez.
    #
    # Surumler arasi kosu, CI kaldirildiktan sonra kalan iki
    # dogrulamadan biri; sessizce kosmamasi en kotu basarisizlik bicimi.
    # Yoklama ucuz, ortam gitignore'da ve yeniden kurmak birkac saniye.
    # Yoklama KONSOL BETIGINI dogrudan calistirir. `uv run ... pytest`
    # ya da `python -c "import pytest"` YETMEZ: ikisi de bozuk bir
    # trampoline'i atlayip basarili doner -- olculdu, `pytest.exe`
    # silinmisken her ikisi de "saglam" dedi.
    betik="$ortam/bin/pytest"
    [ -f "$betik" ] || betik="$ortam/Scripts/pytest.exe"
    if [ -e "$betik" ] && ! "$betik" --version >/dev/null 2>&1; then
      printf '   yan ortam bozuk; yeniden kuruluyor\n'
      rm -rf "$ortam"
    fi
    UV_PROJECT_ENVIRONMENT="$ortam" uv sync --quiet --extra dev --python "$surum"
    UV_PROJECT_ENVIRONMENT="$ortam" uv run --no-sync pytest -q
  done
fi

printf '\nTamam. Bu, deponun tek dogrulamasi.\n'
if [ "$COKLU" -eq 1 ]; then
  printf 'Python %s kosuldu. Isletim sistemi farkini bir makine veremez;\n' "$SURUMLER"
  printf 'yol, surec ve kabuk davranisina dokunan degisikliklerde dikkat.\n'
else
  printf 'Yalnizca bu Python surumunu kapsar; surumler arasi farklar icin:\n'
  printf '  ./scripts/check.sh --pythons\n'
fi
