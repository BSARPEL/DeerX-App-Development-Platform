# Başlarken

[← Belgeler](README.md) · [English](../getting-started.md)

Bu sayfa hiçbir şey varsaymıyor. Python, `uv`, Docker ya da yerel bir model
sunucusu hiç kullanmadıysanız yukarıdan aşağıya takip edin, sonunda çalışan bir
DeerX elde edersiniz. Her adım, işe yarayıp yaramadığını söyleyen bir komutla
biter.

**Toplam süre:** yaklaşık 20 dakika, artı model ağırlıklarınızın inme süresi.

---

## 0. Ne kuruyorsunuz, neden

DeerX doküman güdümlü bir geliştirme ajanı. Ona bir şartname verirsiniz;
analiz eder, araştırır, tasarlar, planlar, kod yazar, test eder ve paketler.
**Sizin** makinenizde çalışır ve **sizin** modelinizle konuşur.

Dört parça, ikisi zorunlu:

| Parça | Zorunlu mu? | Ne yapar | Kurmazsanız… |
|---|---|---|---|
| **Python 3.11+ ve `uv`** | Evet | DeerX'i çalıştırır | Hiçbir şey çalışmaz |
| **Model ucu** | Evet | Asıl zekâ — yerel bir sunucu ya da Anthropic | `ingest` sonrası her faz başlamayı reddeder |
| **SearXNG** (Docker) | Şiddetle önerilir | Araştırma fazı için web araması | Araştırma URL bulmak yerine uydurur. Ölçüldü: bir koşu 14 turu 9 HTTP 404 ve 4 olmayan alan adına harcadı |
| **Google Chrome** | İsteğe bağlı | Ajanın tarayıcı araçları | Mockup'ların ekran görüntüsü alınmaz, canlı arayüz denetimi olmaz |

Bunları tek tek kurmanız gerekmiyor. **`deerx setup` çoğunu yapıyor** — 4. adım.

---

## 1. Python, uv ve Git

`uv` bir Python paket yöneticisi. DeerX onu kullanıyor, böylece sanal ortamları
hiç düşünmüyorsunuz.

**Windows** (PowerShell):

```powershell
winget install Python.Python.3.13 Git.Git; irm https://astral.sh/uv/install.ps1 | iex
```

**macOS**:

```bash
brew install python@3.13 git && curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Linux** (Debian/Ubuntu):

```bash
sudo apt install -y python3 python3-venv git && curl -LsSf https://astral.sh/uv/install.sh | sh
```

Terminali kapatıp yenisini açın; yeni `PATH` böyle okunur. Sonra üçünü de
kontrol edin:

```bash
python --version && git --version && uv --version
```

Python **3.11 veya üstü** gerekiyor. Terminali yeniden açtıktan sonra `uv`
bulunamıyorsa kurulum dizini `PATH`'te değildir — Windows'ta
`%USERPROFILE%\.local\bin`, diğerlerinde `~/.local/bin`.

---

## 2. Kodu alın

```bash
git clone https://github.com/BSARPEL/DeerX-App-Development-Platform.git
```

```bash
cd DeerX-App-Development-Platform && uv sync --extra all
```

`uv sync` `.venv/` oluşturur ve her şeyi kurar. Ekler:

| Ek | Ne katar | Almazsanız… |
|---|---|---|
| `embed` | `fastembed` ile yerel gömme | Hash yedeği kullanılır — deneme için yeterli, gerçek erişim için zayıf |
| `browser` | `playwright`; `browse_page` ve tarayıcı araçları için | Tarayıcı araçları kullanılamaz |
| `dev` | `pytest`, `ruff` | Test paketini koşamazsınız |
| `all` | Hepsi | — |

Kontrol:

```bash
uv run deerx --help
```

---

## 3. Model ucu

DeerX bir modele ihtiyaç duyar. Şunlardan **birini** seçin.

### A seçeneği — yerel sunucu (önerilen, ücretsiz, veriniz dışarı çıkmaz)

OpenAI uyumlu her sunucu olur: **vLLM**, **Ollama**, **LM Studio**,
**llama.cpp**. Gerçek, çalışan bir vLLM örneği — DeerX bununla geliştirildi,
iki GPU üzerinde:

```bash
docker run -d --name qwen3-vllm --gpus all -p 8008:8000 -v /agirliklarin/yolu:/models vllm/vllm-openai:latest /models --served-model-name "qwen3.8 max" --host 0.0.0.0 --port 8000 --tensor-parallel-size 2 --gpu-memory-utilization 0.92 --max-model-len 262144 --enable-prefix-caching --enable-auto-tool-choice --tool-call-parser hermes
```

İki bayrak DeerX'in çalışıp çalışmayacağını belirliyor:

- **`--enable-auto-tool-choice` ve `--tool-call-parser`.** Her ajan araçlar
  üzerinden çalışır. Bunlar olmadan model, DeerX'in araç çağrısı beklediği
  yerde düz metin döndürür ve fazlar sebebi görünmeden başarısız olur.
- **`--served-model-name`.** Buraya ne yazarsanız `deerx.toml` içindeki
  `model_lead` ve `model_worker` ile harfi harfine aynı olmalı.

Sadece çalıştığını görmek istiyorsanız en basit alternatif:

```bash
ollama serve
```

ardından `ollama pull qwen3:8b`, uç olarak `http://127.0.0.1:11434/v1` ve
`model_lead = "qwen3:8b"`.

### B seçeneği — Anthropic

Çalıştıracak sunucu yok. `deerx.toml` içinde `provider = "anthropic"` yapın ve
çalışma alanının `.env` dosyasına `ANTHROPIC_API_KEY=sk-ant-...` koyun.

Aralarındaki farklar için [Model sağlayıcıları](providers.md).

---

## 4. Çalışma alanı kurun, gerisini `setup` yapsın

**Çalışma alanı** tek bir projedir: içinde `deerx.toml`, `.env`, şartnamenizin
duracağı `docs/` ve DeerX'in yönettiği `.deerx/` bulunan bir dizin. Çalışma
alanları bağımsızdır — kendi veritabanı, kendi ayarları, kendi sunucusu.

```bash
uv run deerx setup ~/projeler/projem
```

Bu tek komut:

| Adım | Ne yapar |
|---|---|
| Çalışma alanı | Dizini, `deerx.toml`, `.env` ve `docs/` oluşturur |
| Bağımlılıklar | Eksik ekleri kurar |
| Docker | Docker var mı, bildirir |
| **SearXNG** | Özel bir arama konteyneri başlatır ve **çalışma alanını ona bağlar** |
| Tarayıcı | Kurulu Chrome'unuzu bulur |
| Model ucu | Ucunuzu yoklar ve model adının sunulduğunu doğrular |
| Gömme modeli | İsteğe bağlı olarak gömme modelini indirir (`--with-embedding-model`) |

Bir tablo basar: `✓` (zaten tamam), `+` (şimdi kuruldu), `!` (uyarı, DeerX yine
de koşar), `✗` (engel). Sadece `✗` sizi durdurur.

> **SearXNG neden önemli.** Genel arama motorları otomatik tarayıcıları
> engelliyor — ölçüldü: Bing bağlantıyı kesiyor, DuckDuckGo ve Startpage
> CAPTCHA çıkarıyor, Brave askıya alıyor. Özel bir SearXNG örneğinde bunların
> hiçbiri yok. Arama çalışmayınca araştırma ajanı URL **bulamıyor**, tahmin
> ediyor, ve her tahmin bir tur yakıyor.

Şimdi çalışma alanını modelinize bağlayın. `~/projeler/projem/deerx.toml`:

```toml
[deerx]
provider = "openai"                              # OpenAI uyumlu her uç
openai_base_url = "http://127.0.0.1:8008/v1"
model_lead = "qwen3.8 max"                       # sunucunun sunduğuyla BİREBİR
model_worker = "qwen3.8 max"
```

Ucunuz anahtar istiyorsa `.env` içine koyun (commit edebileceğiniz
`deerx.toml`'a asla):

```bash
# ~/projeler/projem/.env
OPENAI_API_KEY=...
```

---

## 5. Koşmadan önce kontrol

```bash
cd ~/projeler/projem && uv run deerx doctor
```

Tabloyu okuyun:

| Satır | Yeşilse | Kırmızıysa |
|---|---|---|
| Sağlayıcı / Model ucu | Adres erişilebilir | Sunucu çalışmıyor ya da port yanlış |
| Modeller | Uç, `deerx.toml`'daki adları sunuyor | **En sık hata.** `doctor` gerçekte ne sunulduğunu yazar — o adı `deerx.toml`'a kopyalayın |
| Bağlantı | Gerçek bir istek başarılı oldu | Güvenlik duvarı, yanlış şema, ya da model hâlâ yükleniyor |
| Bilgi tabanı | İndeks okunabiliyor | — |

Burada yakalanan bir model adı uyuşmazlığı, aynı şeyi koşunun kırkıncı
dakikasında öğrenmenizi engeller.

---

## 6. İlk koşunuz

Şartnamenizi `docs/` altına koyun. PDF, DOCX, Markdown, HTML ve düz metin
okunur. Depoda örnek var:

```bash
cp examples/ornek-sartname.md ~/projeler/projem/docs/
```

Sonra:

```bash
uv run deerx run --goal "B2B saha servis yönetimi"
```

Varsayılan aralık `ingest → plan`: belgeleri indeksler, analiz eder, araştırır,
boşluk ve riskleri değerlendirir, mockup ve mimari üretir, geliştirme planı
yazar. **Bu aralıkta kod yazılmaz** — önce planı okuyun.

Kodun da yazılması için:

```bash
uv run deerx run --to review
```

`--to live` paketleme, staging ve dağıtıma kadar gider.

### Talimat

`--brief` analiste doğrudan yazdığınız talimattır: neye dikkat edilecek, neyin
pazarlığı yok. Şartname *ne* yapılacağını, talimat *nasıl yaklaşılacağını*
söyler.

```bash
uv run deerx run --goal "..." --brief @talimat.md
```

---

## 7. Web arayüzü

Yukarıdakilerin hepsi tarayıcıda da var — ve bir koşuyu izlemek için tarayıcı
daha iyi.

```bash
uv run deerx serve
```

`http://localhost:8791` açılır. Ya da PID ve günlüğü çalışma alanında tutan,
temiz durdurup yeniden başlatabilen yönetim betikleri:

```bash
./scripts/deerx.sh start
```

```powershell
scripts\deerx.cmd start
```

Windows'ta **`scripts\start.cmd` dosyasına çift tıklayabilirsiniz** — o,
sunucuyu başlatır ve pencereyi açık bırakır, ne olduğunu okuyabilesiniz diye.
(`deerx.cmd`'ye çift tıklamak yardım basıp kapanır; o komut satırı
sarmalayıcısıdır.)

### Hesaplar

Kimlik doğrulama **bir kullanıcı var olduğu anda** devreye girer. Kullanıcısız
bir yerel kurulum eskisi gibi çalışır, ama **kullanıcısı olmayan bir sunucu
dışarı açılamaz**: `--host 0.0.0.0` başlamayı reddeder.

İlk yöneticiyi oluşturun:

```bash
./scripts/deerx.sh passwd
```

```powershell
scripts\deerx.cmd passwd
```

Ya da `scripts\passwd.cmd` dosyasına çift tıklayın. Parolayı iki kez sorar.
**Yazarken ekranda hiçbir şey görünmez, yıldız bile.** Bu normaldir.

### Bu makineye özel varsayılanlar

Her seferinde `-H 0.0.0.0 -w /srv/proje` yazmak yorucu. Örneği kopyalayın:

```bash
cp scripts/deerx.local.conf.example scripts/deerx.local.conf
```

```ini
PORT=8791
HOST=0.0.0.0
WORKSPACE=/srv/projeler/musteri-x
```

İki betik de okur, komut satırı yine kazanır, ve dosya sürüm kontrolüne girmez
— deponun kendi varsayılanı `127.0.0.1` kalır, yani klonlamak kimseyi ağa
açmaz.

---

## 8. Durup soru sorduğunda

Bir ajan yalnızca sizin bilebileceğiniz bir şeye takılırsa **bloke edici soru**
kaydeder ve boru hattı bir sonraki faza girmeden durur:

```
? kapı 2 cevaplanmamış soru boru hattını durdurdu
┌─ Devam etmek için cevabınız gerekiyor ─────────────────────────┐
│ Q-001  ERP sisteminin API dokümanını paylaşabilir misiniz?     │
│    Neden: Entegrasyon tasarlanamıyor.                          │
└────────────────────────────────────────────────────────────────┘
```

```bash
uv run deerx answer Q-001 "Evet, REST API var; OAuth2, 60 istek/dk sınırı."
uv run deerx answer Q-001 --from-file uzun-cevap.md
uv run deerx skip Q-001 -a "REST + OAuth2 varsay"
```

Cevabınız proje hafızasına **ve** bilgi tabanına yazılır; böylece sonraki
fazlar konuşma geçmişi kırpıldıktan sonra da `search_knowledge` ile bulur.

Çıkış kodları betik yazmak için: `0` yolunda, `1` başarısız, `2` cevabınız
bekleniyor.

---

## 9. Ne elde edersiniz

```
.deerx/
├── deerx.db          gereksinim, boşluk, karar, görev, soru, çıktı
├── events.jsonl      olay akışı, yeniden başlatmalar arasında korunur
├── artifacts/        analiz-raporu.md, mimari.md, mockup-*.html, ekran görüntüleri
└── teslimat/         teslimat zip'leri
```

Web arayüzü aynı şeyleri gösterir: **Çıktılar** koşuya göre gruplu (her biri
ait olduğu iş akışını taşır), **İş akışları** adım adım süre ve maliyetle, ve
**Canlı akış** her araç çağrısıyla.

---

## 10. Bir şey ters gittiğinde

- **[Sorun giderme](troubleshooting.md)** — gerçekten yaşanan arızalar için
  belirti, sebep, çözüm.
- `uv run deerx doctor` — her zaman ilk koşulacak şey.
- `.deerx/events.jsonl` — her araç çağrısı ve hata, sırasıyla.
- Ayarlar ekranındaki **denetim günlüğü** kimin ne zaman ne yaptığını ve hangi
  girişlerin reddedildiğini gösterir.

## Sonra

- [Boru hattı](pipeline.md) — 13 fazın her biri ne yapıyor
- [Yapılandırma](configuration.md) — her ayar ve nereden verilebileceği
- [Model sağlayıcıları](providers.md) — vLLM bayrakları ve sağlayıcı farkları
- [Güvenlik](security.md) — `execution = "docker"` dahil: ajanın komutlarını
  makinenizde değil, atılabilir bir konteynerde koşturur
- [Projenin kendi bilgi tabanı](knowledge-base.md) — DeerX hakkındaki soruları
  kendi belgelerinden ve kodundan cevaplatın
