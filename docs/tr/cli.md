# CLI referansı

[← Dokümantasyon](README.md) · [English](../cli.md)

Her komut arayüz dilini takip eder. `deerx --help` iki dilde de mevcut; bkz.
[İki dilli mimari](i18n.md). Komutların arkasındaki sözcükler — çalışma
alanı, iş akışı, koşu, danışman — [Kavramlar](concepts.md) sayfasında.

## Komut dizini

| Komut | Ne yapar |
|---|---|
| `init` · `setup` · `doctor` | Çalışma alanı kur, eksiği kapat, yokla |
| `ingest` · `search` | Doküman indeksle; hibrit ara |
| `run` · `phase` · `implement` | Boru hattını sür |
| `questions` · `answer` · `skip` | Soru kapısı |
| `chat` | Bir iş akışı hakkında danışmanla konuş |
| `status` · `tasks` · `artifacts` | Proje hafızasına bak |
| `package` | Hazırlık kapısı + teslimat zip'i |
| `user …` | Hesaplar |
| `serve` · `mcp` | Diğer iki yüz |

## Çalışma alanı

### `deerx init [yol]`

Çalışma alanı oluşturur: `deerx.toml`, boş bir `.env`, `docs/` ve `.deerx/`.

| Bayrak | |
|---|---|
| `--force` | Var olan `deerx.toml` üzerine yaz |

### `deerx setup [yol]`

Gerekirse çalışma alanını kurar, sonra `doctor`'ın bildireceği boşlukları
kapatır. `doctor` asla yazmaz; `setup` yazar. Ayrım bilinçlidir.

| Bayrak | |
|---|---|
| `--no-deps` | Eksik ekleri yükleme |
| `--no-searxng` | SearXNG konteyneri başlatma |
| `--with-embedding-model` | Gömme modelini ilk aramada değil şimdi indir |

`✓` (zaten tamam), `+` (az önce kuruldu), `!` (uyarı, DeerX yine çalışır)
ve `✗` (engel) ile bir tablo basar. Yalnızca `✗` çıkış kodu `1`'dir.
SearXNG sağlam kalktığında `search_provider`'ı `searxng` yapar ve
`searxng_url` yazar — kurup yok saymak boşa kurmaktır.

### `deerx doctor`

Ortamı kontrol eder: sağlayıcı, uca erişim, yapılandırılan modelin gerçekten
sunulup sunulmadığı, kurulu isteğe bağlı bağımlılıklar ve bilgi tabanı. Bunu
uzun bir koşudan sonra değil, önce çalıştırın.

## Bilgi tabanı

### `deerx ingest [yollar...]`

Doküman ve kodu indeksler. Yol verilmezse çalışma alanını yapılandırılmış
include/exclude desenlerine göre indeksler. Değişmemiş dosyalar atlanır.

| Bayrak | |
|---|---|
| `--force` | Değişmemiş dosyaları da yeniden işle |

### `deerx search "sorgu"`

Hibrit arama — anlamsal artı BM25, sıra bazlı füzyonla.

| Bayrak | |
|---|---|
| `-k N` | Sonuç sayısı (varsayılan 6) |
| `--kind doc\|code\|web\|data` | Kaynak türüne göre filtre, tekrarlanabilir |
| `--full` | İlk 900 karakter yerine parçaların tamamı |

## Boru hattını koşturmak

### `deerx run`

Bir faz aralığı koşturur. Varsayılan `ingest → plan`; analiz, araştırma, boşluk
değerlendirmesi, mockup, mimari ve planı üretir — **kod yazmaz**.

| Bayrak | |
|---|---|
| `--from <faz>` | Başlangıç fazı (varsayılan `ingest`) |
| `--to <faz>` | Bitiş fazı (varsayılan `plan`) |
| `--doc <yol>` | İndekslenecek şartname dosyası/dizini, tekrarlanabilir |
| `--goal "..."` | Kullanıcı hedefi, her ajana bağlam olarak geçer |
| `--brief "..."` \| `--brief @dosya.md` | Analiste serbest talimat |
| `--force` | Tamamlanmış fazları yeniden koştur |
| `--yes` / `-y` | Bu koşu için `approval_mode=auto` |
| `--dry-run` | Yazmaları uygulamak yerine raporla |

```bash
uv run deerx run --to review --goal "B2B saha servis yonetimi"
```

### `deerx phase <ad>`

Tek bir faz koşturur. `--force`, `--yes` yukarıdaki gibi.

### `deerx implement`

Uygulama fazını koşturur.

| Bayrak | |
|---|---|
| `--task T-003` | Yalnızca bu görevi uygula |
| `--yes` / `-y` | Onayları atla |

## Sorular

### `deerx questions`

Açık soruları listeler. `--all` cevaplanmışları da gösterir.

### `deerx answer <anahtar> "metin"`

Bir soruyu cevaplar. Cevap proje hafızasına **ve** bilgi tabanına gider.

| Bayrak | |
|---|---|
| `--from-file` / `-f <yol>` | Cevabı dosyadan oku |

`--from-file` `@yol` öneki yerine açık bir bayraktır, çünkü bir cevap pekâlâ `@`
ile başlayabilir — `deerx answer Q-001 "@firma.com adresine gider"` çöküyordu.

### `deerx skip <anahtar>`

Bir varsayımla ilerler.

| Bayrak | |
|---|---|
| `--assumption` / `-a "..."` | Kaydedilecek varsayım; verilmezse ajan kendi kurar |

## Bir iş akışı hakkında konuşmak

### `deerx chat <iş-akışı> ["ileti"]`

Danışmanla **tek bir** iş akışı hakkında konuşma. Danışman o iş akışı
hakkındaki soruları cevaplar ve isterseniz başlığını, hedefini ya da
talimatını değiştirir, açık bir soruyu kapatır. Komut çalıştıramaz, proje
dosyası yazamaz. Bkz. [Kavramlar — Danışman](concepts.md#danışman).

`<iş-akışı>` sıralı numaradır (`2`, `#2`) ya da ham kimlik.

| Bayrak | |
|---|---|
| *(ileti yok)* | Şimdiye kadarki konuşmayı bas |
| `--history` | Boş iletiyle aynı |
| `--clear` | Konuşmayı sil |

```bash
uv run deerx chat 2 "Plani hala ne bloke ediyor?"
uv run deerx chat 2 --history
uv run deerx chat 2 --clear
```

## İnceleme

### `deerx status`

Durum ve maliyetle faz tablosu, artı sayımlar: doküman, gereksinim, boşluk,
karar, soru, görev, çıktı.

### `deerx tasks`

Durum, tür, başlık ve bağımlılıklarla görev listesi. ✓ işareti bağımlılıkları
tamam olan görevleri gösterir.

| Bayrak | |
|---|---|
| `--status pending\|running\|done\|blocked\|failed` | Filtre |

### `deerx artifacts [ad]`

Üretilen çıktıları listeler ya da birini gösterir. Markdown biçimlendirilir.

## Teslimat

### `deerx package`

Hazırlık kapısını yoklar ve teslimat zip'ini üretir. Bkz.
[Teslimat paketleri](delivery.md).

| Bayrak | |
|---|---|
| `--force` | Kapıya rağmen paketle; engeller manifestoya yazılır |
| `--output` / `-o <dizin>` | Zip'in yazılacağı yer |

## Kullanıcılar

Kimlik doğrulama, bir kullanıcı var olduğu anda devreye girer.

```bash
deerx user add sarpel --admin    # ilk hesap her zaman ana yönetici olur
deerx user list
deerx user passwd sarpel         # açık oturumların hepsi düşer
deerx user ensure admin          # yoksa kur, varsa parolasını sıfırla
deerx user disable ekip          # silmeden kapat
deerx user enable ekip
deerx user remove ekip --yes
```

Parolalar sorulur, argümanla alınmaz — argüman kabuk geçmişine ve `ps`
çıktısına yazılırdı.

`ensure` üç durumu tek komutta toplar: hiç kullanıcı yoksa ana yöneticiyi
kurar, hesap yoksa ekler, varsa parolasını sıfırlar. Betikler için: alternatifi
`user list` çıktısını ayrıştırmaktı, o da biçimi kütüphane sürümüyle değişen
bir Rich tablosu.

### Unutulan yönetici parolasını sıfırlama

```bash
./scripts/deerx.sh passwd            # Linux, macOS
scripts\deerx.cmd passwd             # Windows
```

Windows'ta `scripts\passwd.cmd` dosyasına çift tıklamak da aynı şeyi yapar.
`admin` dışında bir hesap için `-a ad` / `-Account ad` ekleyin.

Betik parolayı kendisi okur — iki kez, yankı kapalıyken — ve boru hattıyla
`deerx user ensure --stdin` komutuna verir. Doğrudan `deerx user passwd`
çağırmamasının bir sebebi var: o komut parolayı `getpass` ile soruyor, `getpass`
ise Windows'ta konsolu **doğrudan** okuyor ve boru hattındaki veriyi hiç
görmüyor — bir betikten beslendiğinde çıktısız kilitleniyor.

Yazarken ekranda hiçbir şey görünmez, yıldız bile. Betikler bunu sormadan önce
söylüyor: tuşları yutan bir soru, bozuk bir soru gibi okunuyor.

## Sunucular

### `deerx serve`

Web arayüzünü başlatır.

| Bayrak | Varsayılan |
|---|---|
| `--host` | `127.0.0.1` |
| `--port` / `-p` | `8791` |
| `--workspace` | En yakın çalışma alanı |
| `--open` / `--no-open` | Tarayıcı aç |

Loopback dışı bir `--host`, kullanıcı tanımlı değilse başlamayı reddeder.

### `deerx mcp`

MCP sunucusunu stdio üzerinde çalıştırır. `--workspace` hangi çalışma alanına
hizmet edeceğini belirler. Bkz. [MCP sunucusu](mcp.md).

## Çıkış kodları

| Kod | Anlamı |
|---|---|
| `0` | Başarılı |
| `1` | Başarısız |
| `2` | Cevabınız bekleniyor (soru kapısı) ya da hazırlık kapısı paketlemeyi engelledi |

Üçüncü kod, bir betiğin "bu bozuldu" ile "bunun insana ihtiyacı var" durumlarını
ayırt edebilmesi için var.

```bash
uv run deerx run --to review
case $? in
  0) echo "tamam" ;;
  2) uv run deerx questions ;;
  *) echo "basarisiz" ;;
esac
```

## Yönetim betikleri

Üç işletim sisteminde de aynı dört komut. PID ve günlük çalışma alanının
`.deerx/` dizininde tutulur, yani her çalışma alanı kendi sunucusunu yönetir.

```bash
./scripts/deerx.sh start          # Linux, macOS
scripts\deerx.cmd start           # Windows
```

`stop` · `restart` · `status` · `logs [-f]`

Seçenekler: `-p 9000` (port), `-w ./demo` (çalışma alanı), `-H 0.0.0.0` (adres).

`.cmd` sarmalayıcısı PowerShell politikası kısıtlıyken de çalışır — yalnızca o
çağrı için `-ExecutionPolicy Bypass` geçer, makine ayarına dokunmaz.
PowerShell'den doğrudan da olur: `.\scripts\deerx.ps1 restart -Port 9000`.

### Tek bir makineye özel varsayılanlar

Her seferinde aynı seçenekleri yazmak yorucu, betiklerin varsayılanını
değiştirmek ise çözüm değil: bu depo herkese açık ve varsayılanı `0.0.0.0`
yapmak klonlayan herkesin DeerX'ini ağa açardı. Örneği kopyalayın:

```bash
cp scripts/deerx.local.conf.example scripts/deerx.local.conf
```

```ini
PORT=8791
HOST=0.0.0.0
WORKSPACE=/srv/projeler/musteri-x
```

İki betik de okur. **Komut satırı yine kazanır** — `deerx.sh start -p 9000`
dosyadaki portu ezer — ve dosyadan bir değer okunduğunda betik bunu bir
satırla söyler, yani `0.0.0.0`'a bağlanmış bir sunucu sürpriz olmaz. Dosya
sürüm kontrolüne girmez, ve kaynak alınmak yerine satır satır okunur: bir
ayar dosyasının komut çalıştırabilmesi gerekmiyor.

Loopback dışı bir adrese bağlanmak için en az bir hesap gerekir; yoksa `serve`
açık bir sunucu bırakmaktansa başlamayı reddeder.

### Betiklerin doğru yaptığı şeyler

| Durum | Davranış |
|---|---|
| PID geri dönüşmüş, başka bir sürece ait | Komut satırı doğrulanır; yabancı süreç **öldürülmez** |
| `deerx.exe` sarmalayıcı, sunucu ayrı süreç | PID porttan çözülür; sarmalayıcı öldürülüp yetim bırakılmaz |
| Sunucu betik dışında başlatılmış | `status` bunu söyler, "durmuş" demez |
| Portu alakasız bir program tutuyor | "DeerX değil" der; `start` başka port önerir |
| Çalışma alanı başka portta çalışıyor | Gerçek port yazılır, istenen port değil |

`start`, süreç var olduğunda değil sunucu **yanıt verdiğinde** başarılı sayılır;
erişilemezse günlüğün son satırları basılır.

Yoklanan uç `/api/auth/status`. Kimlik doğrulama açıkken korumalı bir uç 401
döner ve hem `curl -f` hem `Invoke-WebRequest` bunu hata sayar — sağlam bir
sunucu yanıtsız görünürdü. `tests/test_scripts.py` bunu sabitliyor: betiklerin
yokladığı her yol `PUBLIC_PATHS` içinde olmalı.
