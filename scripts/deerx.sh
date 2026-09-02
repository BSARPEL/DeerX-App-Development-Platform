#!/usr/bin/env sh
# DeerX web sunucusu - baslat / durdur / yeniden baslat.  (Linux ve macOS)
#
#   ./scripts/deerx.sh start          calisma alani = bulundugunuz dizin
#   ./scripts/deerx.sh stop
#   ./scripts/deerx.sh restart
#   ./scripts/deerx.sh status
#   ./scripts/deerx.sh logs
#
#   -p 9000        baska port          -H 0.0.0.0   baska adres (aga acar)
#   -w ./demo      baska calisma alani  -f           logu takip et (logs)
#
# Her seferinde ayni secenekleri yazmamak icin: scripts/deerx.local.conf
# (ornegi deerx.local.conf.example; surum kontrolune girmez).
#
# PID ve gunluk calisma alaninin `.deerx/` dizininde tutulur; boylece her
# calisma alani kendi sunucusunu bagimsiz yonetir.

set -eu

PORT=8791
HOST=127.0.0.1
WORKSPACE=$(pwd)
ACCOUNT=admin
FOLLOW=0
STOP_GRACE=10          # TERM sonrasi KILL'e kadar beklenen saniye
START_TIMEOUT=90       # saglik yoklamasi (ilk acilista gomme modeli yuklenir)

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

# Yerel varsayilanlar. Bu dosya surum kontrolune GIRMEZ ve bilerek oyle:
# depo herkese acik, varsayilani 0.0.0.0 yapmak klonlayan herkesin
# DeerX'ini aga acardi. Ornegi icin deerx.local.conf.example.
#
# Onceklik: komut satiri > bu dosya > yukaridaki varsayilanlar.
#
# Dosya `.` ile yuklenmiyor, SATIR SATIR okunuyor: kaynak almak, ayar
# dosyasina yazilan her seyi kabuk komutu olarak calistirmak demekti.
LOCAL_CONF="$SCRIPT_DIR/deerx.local.conf"
LOCAL_USED=""
if [ -f "$LOCAL_CONF" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|\#*) continue ;; esac
    case "$line" in *=*) ;; *) continue ;; esac
    key=${line%%=*}
    val=${line#*=}
    # `[:space:]` POSIX sinif adi; her yerde ayni sey demek. `\t` yerine
    # onu kullaniyoruz cunku BSD arac zinciri (macOS) `\t`yi kacis dizisi
    # SAYMAZ: `tr -d ' \t'` orada bosluk, ters bolu ve 't' HARFINI siler,
    # `sed 's/^[ \t]*//'` de bir degerin basindaki 't'yi yer. `tcp://...`
    # yazan bir ayar macOS'ta sessizce `cp://...` olurdu.
    key=$(printf '%s' "$key" | tr -d '[:space:]')
    val=$(printf '%s' "$val" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//; s/^"//; s/"$//')
    case "$key" in
      PORT)      PORT=$val;      LOCAL_USED="$LOCAL_USED PORT" ;;
      HOST)      HOST=$val;      LOCAL_USED="$LOCAL_USED HOST" ;;
      WORKSPACE) WORKSPACE=$val; LOCAL_USED="$LOCAL_USED WORKSPACE" ;;
      *) printf 'UYARI: %s: taninmayan anahtar "%s"\n' "$LOCAL_CONF" "$key" >&2 ;;
    esac
  done < "$LOCAL_CONF"
fi

COMMAND=${1:-help}
[ $# -gt 0 ] && shift || true

while getopts "p:H:w:a:f" opt 2>/dev/null; do
  case "$opt" in
    p) PORT=$OPTARG ;;
    H) HOST=$OPTARG ;;
    w) WORKSPACE=$OPTARG ;;
    a) ACCOUNT=$OPTARG ;;
    f) FOLLOW=1 ;;
    *) ;;
  esac
done

WORKSPACE=$(CDPATH= cd -- "$WORKSPACE" 2>/dev/null && pwd) || {
  printf 'HATA: calisma alani bulunamadi: %s\n' "$WORKSPACE" >&2; exit 1; }

# Yerel dosyadan gelen degerler SOYLENIR. Sessiz kalsaydi biri
# `deerx.sh start` yazip sunucunun neden 0.0.0.0'a baglandigini
# anlamazdi -- ve bunu ancak disaridan biri girdiginde fark ederdi.
[ -n "$LOCAL_USED" ] && printf 'Yerel ayar: %s ->%s\n' "$LOCAL_CONF" "$LOCAL_USED"

DATA_DIR="$WORKSPACE/.deerx"
PID_FILE="$DATA_DIR/server.pid"
LOG_FILE="$DATA_DIR/server.log"
URL="http://$HOST:$PORT"

# ── Yardimcilar ─────────────────────────────────────────────────────────── #

say()  { printf '%s\n' "$*"; }
die()  { printf 'HATA: %s\n' "$*" >&2; exit 1; }

# Kurulu deerx komutunu bul. Sanal ortam yoksa `uv run` ile dene.
#
# IKI YERLESIM de aranir. Sanal ortamin ikili dizini POSIX'te `bin/`,
# Windows'ta `Scripts/`; bu betik Windows'ta da kosuyor (Git Bash). Yalnizca
# `bin/`e bakildiginde kurulum YERINDE OLDUGU HALDE bulunamiyor, betik
# "kurulu degil" yoluna dusuyor ve `uv run` ortami tazelemeye kalkiyordu.
# Sunucu calisirken bu, `deerx.exe` kilitli oldugu icin her baslatmayi
# "os error 32" ile olduruyordu -- olculdu.
resolve_launcher() {
  if [ -x "$ROOT/.venv/bin/deerx" ]; then
    LAUNCHER="$ROOT/.venv/bin/deerx"; LAUNCH_ARGS=""
  elif [ -x "$ROOT/.venv/Scripts/deerx.exe" ]; then
    LAUNCHER="$ROOT/.venv/Scripts/deerx.exe"; LAUNCH_ARGS=""
  elif command -v uv >/dev/null 2>&1; then
    LAUNCHER="uv"; LAUNCH_ARGS="run --project $ROOT deerx"
  elif command -v deerx >/dev/null 2>&1; then
    LAUNCHER="deerx"; LAUNCH_ARGS=""
  else
    die "deerx bulunamadi. Kurulum icin:  $0 setup"
  fi
}

# PID gercekten *bizim* sunucumuz mu? PID'ler geri donusur; yanlis sureci
# oldurmemek icin komut satirini da dogrularz. Yalnizca "deerx" gecmesi
# yetmez: bu betigin kendi komut satirinda da geciyor, yani betik kendini
# sunucu sanabilirdi. "serve" da araniyor, betigin kendisi disarida birakiliyor.
is_our_server() {
  pid=$1
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  cmd=$(ps -p "$pid" -o args= 2>/dev/null || printf '')
  case "$cmd" in
    *deerx.sh*|*deerx.ps1*|*deerx.cmd*) return 1 ;;
    *deerx*serve*) return 0 ;;
    *) return 1 ;;
  esac
}

running_pid() {
  [ -f "$PID_FILE" ] || return 1
  pid=$(cat "$PID_FILE" 2>/dev/null || printf '')
  if is_our_server "$pid"; then
    printf '%s' "$pid"
    return 0
  fi
  # Bayat PID dosyasi: surec olmus ya da baska bir surece ait.
  rm -f "$PID_FILE"
  return 1
}

# Portu gercekten dinleyen surec. Baslatici bir sarmalayici olabilir ve asil
# sunucuyu ayri bir surec olarak baslatabilir; sarmalayiciyi oldurmek cocugu
# her zaman kapatmaz ve port dolu kalir. Bu yuzden PID'i porttan cozeriz.
port_owner() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null | head -n 1
  elif command -v ss >/dev/null 2>&1; then
    ss -lptnH "sport = :$PORT" 2>/dev/null |
      sed -n 's/.*pid=\([0-9]*\).*/\1/p' | head -n 1
  else
    printf ''
  fi
}

# Verilen surecin dinledigi port. PID dosyasi calisma alanina ait, port ise
# bir parametre; ikisi uyusmayabilir. Bunu sormadan "zaten calisiyor" demek,
# istenen adresi -- calismadigi halde -- calisiyor gibi gosterirdi.
listen_port() {
  pid=$1
  [ -n "$pid" ] || { printf ''; return 0; }
  if command -v lsof >/dev/null 2>&1; then
    lsof -aPi -p "$pid" -sTCP:LISTEN -Fn 2>/dev/null |
      sed -n 's/^n.*:\([0-9][0-9]*\)$/\1/p' | head -n 1
  elif command -v ss >/dev/null 2>&1; then
    # `ss -lptnH` cikisinda 4. sutun adres:port, sonuncu sutun users:((...pid=N,...))
    ss -lptnH 2>/dev/null | awk -v pat="pid=$pid," '
      index($0, pat) { n = split($4, a, ":"); print a[n]; exit }'
  else
    printf ''
  fi
}

# Yoklayacak bir arac var mi? Yoksa "yanit vermiyor" ile "bakamiyorum"
# birbirine karisir ve durum raporu uydurma bilgi verir.
have_probe() {
  command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1
}

http_ok() {
  probe_url=${1:-$URL}
  # `/api/overview` yoklanmaz: kimlik dogrulama acikken 401 doner ve
  # `curl -f` bunu hata sayar, yani sunucu saglamken "yanit yok" denirdi.
  # `/api/auth/status` bilerek herkese acik ve statik bir dosya degil -
  # uygulamanin kendisini calistirir.
  if command -v curl >/dev/null 2>&1; then
    # -f yok: herhangi bir HTTP yaniti sunucunun ayakta oldugunu gosterir.
    # Yalnizca baglantinin hic kurulamamasi "kapali" demektir.
    curl -sS -o /dev/null --max-time 3 "$probe_url/api/auth/status" >/dev/null 2>&1
  elif command -v wget >/dev/null 2>&1; then
    rc=0
    wget -q -O /dev/null --tries=1 --timeout=3 "$probe_url/api/auth/status" >/dev/null 2>&1 || rc=$?
    # 0 = yanit alindi, 8 = sunucu bir hata durumu dondurdu; ikisi de ayakta.
    [ "$rc" -eq 0 ] || [ "$rc" -eq 8 ]
  else
    return 1
  fi
}

# ── Komutlar ────────────────────────────────────────────────────────────── #

cmd_setup() {
  # Kurulum mantigi `deerx setup` icinde: tek uygulama, iki betik.
  # Bagimliliklar henuz kurulu olmayabilir, o yuzden `uv run` yolu da
  # kabul ediliyor.
  if [ -x "$ROOT/.venv/bin/deerx" ]; then
    "$ROOT/.venv/bin/deerx" setup "$WORKSPACE" "$@"
  elif command -v uv >/dev/null 2>&1; then
    uv run --project "$ROOT" deerx setup "$WORKSPACE" "$@"
  else
    die "Once uv kurun: https://docs.astral.sh/uv/  (ya da: pip install -e $ROOT)"
  fi
}

# Yonetici parolasini kurar ya da sifirlar.
#
# `deerx user passwd` dogrudan kullanilamiyor: parolayi `getpass` ile
# soruyor ve bir betikten beslenemiyor. `--stdin` bunun icin eklendi.
#
# Parola ARGUMAN olarak gecirilmiyor: arguman `ps` ciktisinda gorunur ve
# kabuk gecmisine yazilir. Standart girdi ikisine de dusmez.
cmd_passwd() {
  resolve_launcher
  account=${ACCOUNT:-admin}

  say "Hesap        : $account"
  say "Calisma alani: $WORKSPACE"
  say ""
  say "Parolayi yazarken EKRANDA HICBIR SEY GORUNMEZ - ne harf ne yildiz."
  say "Bu normaldir; yazip Enter'a basin."
  say ""

  # Yankiyi kapatirken bir kesinti (Ctrl-C) terminali yanki kapali
  # birakabilir; kullanici bundan sonra ne yazdigini goremez.
  eski_stty=$(stty -g 2>/dev/null || printf '')
  [ -n "$eski_stty" ] && trap 'stty "$eski_stty" 2>/dev/null; exit 130' INT TERM

  while :; do
    [ -n "$eski_stty" ] && stty -echo 2>/dev/null
    printf 'Yeni parola: '
    IFS= read -r pw1 || { [ -n "$eski_stty" ] && stty "$eski_stty" 2>/dev/null; die "girdi okunamadi"; }
    printf '\n'
    printf 'Yeni parola (tekrar): '
    IFS= read -r pw2 || { [ -n "$eski_stty" ] && stty "$eski_stty" 2>/dev/null; die "girdi okunamadi"; }
    printf '\n'
    [ -n "$eski_stty" ] && stty "$eski_stty" 2>/dev/null

    if [ "$pw1" != "$pw2" ]; then
      say "Iki parola ayni degil. Tekrar deneyin."
      continue
    fi
    # Uzunlugu burada da bakiyoruz: sunucunun reddini gormek icin
    # parolayi iki kez yazdirmak gereksiz bir ceza olurdu.
    if [ ${#pw1} -lt 8 ]; then
      say "Parola en az 8 karakter olmali. Tekrar deneyin."
      continue
    fi
    break
  done
  [ -n "$eski_stty" ] && trap - INT TERM

  printf '%s\n' "$pw1" | $LAUNCHER $LAUNCH_ARGS user ensure "$account" --admin --stdin
  rc=$?
  pw1=''; pw2=''
  [ $rc -eq 0 ] || die "parola ayarlanamadi"
  say ""
  say "Bitti. Acik oturumlarin hepsi dustu; yeniden giris gerekiyor."
}

cmd_start() {
  if pid=$(running_pid); then
    on_port=$(listen_port "$pid")
    if [ -n "$on_port" ] && [ "$on_port" != "$PORT" ]; then
      # Bir calisma alani = bir sunucu: ayni SQLite dosyasini iki sunucu
      # arasinda paylastirmak istenmez. Ama istenen portta calistigini
      # soylemek de yanlis olur; gercek adresi veriyoruz.
      say "Bu calisma alani zaten $on_port portunda calisiyor (PID $pid)."
      say "      Once durdurun:  stop -p $on_port"
      exit 1
    fi
    say "Zaten calisiyor (PID $pid) - $URL"
    return 0
  fi

  # PID dosyasi yok ama port dolu olabilir: sunucu bu betik disinda
  # baslatilmis ya da PID dosyasi bayatlamis olabilir. Bunu simdi anlamak,
  # 90 saniye bekleyip "yanit vermedi" demekten iyidir.
  owner=$(port_owner)
  if [ -n "$owner" ]; then
    if is_our_server "$owner"; then
      mkdir -p "$DATA_DIR"
      printf '%s' "$owner" >"$PID_FILE"
      say "Zaten calisiyor (PID $owner) - $URL   (PID dosyasi tazelendi)"
      return 0
    fi
    name=$(ps -p "$owner" -o comm= 2>/dev/null || printf 'bilinmiyor')
    say "HATA: $PORT portunu $name kullaniyor (PID $owner)."
    say "      DeerX hep $PORT portunda calisir. Once o programi kapatin:"
    say "        kill $owner"
    say "      (zorunluysa gecici olarak: -p 9000)"
    exit 1
  fi

  resolve_launcher
  mkdir -p "$DATA_DIR"

  say "Baslatiliyor...  calisma alani: $WORKSPACE"
  printf '\n--- baslatiliyor: %s ---\n' "$(date '+%Y-%m-%d %H:%M:%S')" >>"$LOG_FILE"

  # PID dogru yakalanmali. Onceki hali `( cd ... && nohup ... & printf $! )`
  # idi: `&&` ile baglanan bir liste toptan arka plana atilir, dolayisiyla
  # `$!` araya giren kabugun PID'ini verir. O kabuk hemen oldugu icin saglik
  # dongusu "surec olmus" diye erken pes eder, PID dosyasini siler ve gercek
  # sunucu yetim kalarak portu tutmaya devam ederdi.
  cd "$WORKSPACE" || die "calisma alanina girilemedi: $WORKSPACE"
  # shellcheck disable=SC2086
  nohup "$LAUNCHER" $LAUNCH_ARGS serve --host "$HOST" --port "$PORT" >>"$LOG_FILE" 2>&1 &
  pid=$!
  printf '%s' "$pid" >"$PID_FILE"

  if ! have_probe; then
    # curl da wget da yok: ayakta oldugunu dogrulayamayiz. Dogrulanmamis bir
    # "Hazir" yazmaktansa ne bilmedigimizi soyleriz.
    say "Baslatildi (PID $pid) - $URL"
    say "NOT: curl/wget yok; saglik yoklamasi yapilamadi. Gunluk: $LOG_FILE"
    return 0
  fi

  waited=0
  while [ "$waited" -lt "$START_TIMEOUT" ]; do
    # Baslatici bir sarmalayici olabilir ve cikmis olabilir; asil sunucu yine
    # de ayakta olabilir. Once porta bakariz, sonra "oldu" deriz.
    if ! kill -0 "$pid" 2>/dev/null && [ -z "$(port_owner)" ]; then
      say "Sunucu acilamadi. Gunlugun son satirlari:"
      tail -n 20 "$LOG_FILE" 2>/dev/null || true
      rm -f "$PID_FILE"
      exit 1
    fi
    if http_ok; then
      owner=$(port_owner)
      if [ -n "$owner" ] && is_our_server "$owner"; then
        printf '%s' "$owner" >"$PID_FILE"
        pid=$owner
      fi
      say "Hazir - $URL   (PID $pid, gunluk: $LOG_FILE)"
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  say "Surec ayakta (PID $pid) ama $START_TIMEOUT saniyede yanit vermedi."
  say "Gunluge bakin: $LOG_FILE"
  exit 1
}

cmd_stop() {
  target=""
  if pid=$(running_pid); then
    target=$pid
  fi

  # PID dosyasi yoksa ya da bayatsa bile port dolu olabilir: sunucu bu betik
  # disinda baslatilmis olabilir. O durumda "calisan sunucu yok" deyip
  # cikmak, portu tutan sureci gorunmez birakirdi.
  owner=$(port_owner)
  if [ -z "$target" ] && [ -n "$owner" ]; then
    if is_our_server "$owner"; then
      target=$owner
    else
      say "UYARI: $URL dinleniyor (PID $owner) ama bu surec DeerX degil."
      say "       Dokunulmadi."
    fi
  fi

  if [ -z "$target" ]; then
    say "Calisan sunucu yok."
    rm -f "$PID_FILE"
    return 0
  fi

  say "Durduruluyor (PID $target)..."
  kill "$target" 2>/dev/null || true

  waited=0
  while [ "$waited" -lt "$STOP_GRACE" ]; do
    kill -0 "$target" 2>/dev/null || break
    sleep 1
    waited=$((waited + 1))
  done

  if kill -0 "$target" 2>/dev/null; then
    say "Nazikce kapanmadi; zorla sonlandiriliyor."
    kill -9 "$target" 2>/dev/null || true
    sleep 1
  fi
  rm -f "$PID_FILE"

  # Surec oldu diye port bosaldi sayilmaz; yetim bir cocuk tutuyor olabilir.
  lingering=$(port_owner)
  if [ -n "$lingering" ]; then
    if is_our_server "$lingering"; then
      say "Yetim DeerX sureci kapatiliyor (PID $lingering)."
      kill -9 "$lingering" 2>/dev/null || true
      sleep 1
    else
      say "UYARI: $URL hala dinleniyor (PID $lingering)."
      say "       Bu surec DeerX degil; dokunulmadi."
    fi
  fi
  say "Durduruldu."
}

cmd_restart() {
  cmd_stop
  cmd_start
}

cmd_status() {
  owner=$(port_owner)
  if pid=$(running_pid); then
    # Gercekte dinlenen port yazilir, istenen port degil: ikisi ayrildiginda
    # "calisiyor" satirinin altina yanlis adres dusmesin.
    on_port=$(listen_port "$pid")
    shown=$URL
    [ -n "$on_port" ] && shown="http://$HOST:$on_port"
    printf 'Durum   : calisiyor (PID %s)\n' "$pid"
    printf 'Adres   : %s\n' "$shown"
    if [ -n "$on_port" ] && [ "$on_port" != "$PORT" ]; then
      printf 'NOT     : sorulan port %s, dinlenen port %s.\n' "$PORT" "$on_port"
    fi
    if ! have_probe; then
      printf 'HTTP    : yoklanamadi (curl/wget yok)\n'
    elif http_ok "$shown"; then
      printf 'HTTP    : yanit veriyor\n'
    else
      printf 'HTTP    : YANIT YOK (aciliyor olabilir)\n'
    fi
  elif [ -n "$owner" ] || { have_probe && http_ok; }; then
    # PID dosyasi yok ama adres dinleniyor: sunucu bu betikle baslatilmamis.
    # "Durmus" demek yaniltici olurdu - calisan bir sunucuyu durmus
    # gostermek, insani ikinci bir tane baslatmaya ve "port dolu" hatasina
    # goturur.
    printf 'Durum   : durmus (PID dosyasi yok)\n'
    # Portu tutan sey bizim sunucumuz mu, alakasiz bir program mi? Ikisine
    # ayni cumleyi kurmak yaniltir: birinde "devral" dogru tavsiye, digerinde
    # bizim isimiz olmayan bir sureci kapatmayi onermek olur.
    if [ -n "$owner" ] && ! is_our_server "$owner"; then
      printf 'DIKKAT  : %s portunu baska bir program kullaniyor (PID %s).\n' "$PORT" "$owner"
      printf '          Bu bir DeerX sunucusu degil; dokunulmadi.\n'
    else
      if [ -n "$owner" ]; then
        printf 'DIKKAT  : %s dinleniyor (PID %s) - sunucu bu betik disinda baslatilmis.\n' "$URL" "$owner"
      else
        printf 'DIKKAT  : %s yanit veriyor - sunucu bu betik disinda baslatilmis.\n' "$URL"
      fi
      printf '          Yonetimi devralmak icin:  stop, sonra start.\n'
    fi
  else
    printf 'Durum   : durmus\n'
  fi
  # Adres durmus halde de yazilir: yerel ayar dosyasi portu ve adresi
  # gorunmeden degistirebiliyor, yani tek basina "durmus" hangi adres
  # hakkinda konustugunu soylemiyordu.
  printf 'Adres   : %s\n' "$URL"
  printf 'Alan    : %s\n' "$WORKSPACE"
  printf 'Gunluk  : %s\n' "$LOG_FILE"
}

cmd_logs() {
  [ -f "$LOG_FILE" ] || die "Gunluk yok: $LOG_FILE"
  if [ "$FOLLOW" -eq 1 ]; then
    tail -f "$LOG_FILE"
  else
    tail -n 60 "$LOG_FILE"
  fi
}

# Yardim metni burada, satir numarasina bagli `sed` ile basligi kesip
# yapistirmak yerine: baslik bir satir kaysa yardim sessizce bozuluyordu.
cmd_help() {
  cat <<'EOF'
DeerX web sunucusu  (Linux ve macOS)

  ./scripts/deerx.sh setup      bagimlilik, SearXNG, calisma alani
  ./scripts/deerx.sh start      calisma alani = bulundugunuz dizin
  ./scripts/deerx.sh stop
  ./scripts/deerx.sh restart
  ./scripts/deerx.sh status
  ./scripts/deerx.sh logs [-f]
  ./scripts/deerx.sh passwd [-a admin]   yonetici parolasini kur/sifirla

Secenekler
  -p 9000        baska port
  -H 0.0.0.0     baska adres. Sunucuyu AGA ACAR: en az bir kullanici
                 tanimli olmali, yoksa baslamaz.  deerx user add <ad> --admin
  -w ./demo      baska calisma alani
  -a sarpel      hangi hesap (yalnizca passwd; varsayilan: admin)
  -f             logu takip et (yalnizca logs)

Her seferinde ayni secenekleri yazmamak icin scripts/deerx.local.conf
olusturun (ornegi: deerx.local.conf.example). Surum kontrolune girmez.
Onceklik: komut satiri > o dosya > varsayilanlar.

PID ve gunluk calisma alaninin .deerx/ dizininde tutulur.
EOF
}

case "$COMMAND" in
  setup)   cmd_setup ;;
  passwd)  cmd_passwd ;;
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_restart ;;
  status)  cmd_status ;;
  logs)    cmd_logs ;;
  help|-h|--help) cmd_help ;;
  *) printf 'Bilinmeyen komut: %s\n\n' "$COMMAND" >&2; cmd_help; exit 1 ;;
esac
