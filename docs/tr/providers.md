# Model sağlayıcıları

[← Dokümantasyon](README.md) · [English](../providers.md)

DeerX iki sağlayıcıyla çalışır. **Varsayılan yerel bir OpenAI-uyumlu uçtur** —
token maliyeti sıfır, dokümanlar makineden çıkmaz.

| Sağlayıcı | Kapsadığı | Gereken ayar |
|---|---|---|
| `openai` *(varsayılan)* | vLLM, Ollama, LM Studio, llama.cpp, OpenAI | `openai_base_url` |
| `anthropic` | Claude API | `ANTHROPIC_API_KEY` |

## Araç çağırma şart

DeerX'in tamamı araçlar üzerine kurulu. Araç çağıramayan bir model tek bir fazı
bile koşamaz — boru hattının kayıt beklediği yerde düzyazı üretir.

vLLM için bu, `--enable-auto-tool-choice` ve modelinize uyan bir
`--tool-call-parser` demektir:

```bash
docker run --gpus all -p 8008:8000 \
  vllm/vllm-openai:latest /models/local \
  --served-model-name "qwen3-coder-30b" \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder \
  --max-model-len 262144
```

Sonra DeerX'i ona yönlendirin:

```toml
# deerx.toml
[deerx]
provider = "openai"
model_lead = "qwen3-coder-30b"
model_worker = "qwen3-coder-30b"
```

```bash
# .env
DEERX_OPENAI_BASE_URL=http://127.0.0.1:8008/v1
OPENAI_API_KEY=...          # yalnızca ucunuz anahtar istiyorsa
```

Uzun bir koşuya başlamadan doğrulayın:

```bash
uv run deerx doctor
```

`doctor` ucun gerçekten sunduğu modelleri listeler ve yapılandırmanızdaki ad
aralarında değilse söyler. Model adı uyuşmazlığı en sık yapılan kurulum
hatasıdır ve aksi halde kırkıncı dakikada ortaya çıkar.

## Bağlam penceresi

Yerel bir uç, `max_tokens` artı prompt penceresini aşan bir isteği uzun bir
koşunun ortasında 400 ile reddeder.

DeerX pencereyi kendisi keşfeder: ucun model listesinden `max_model_len` okur,
girdi boyutunu tahmin eder ve istenen çıktıyı istek sığacak şekilde kırpar. Uç
yine de reddederse kendi hatasındaki sayılarla bir kez daha denenir.

Keşif mümkün değilse açıkça verin:

```toml
[deerx]
context_window = 262144
max_tokens = 64000
```

Bir de tutarlılık kontrolü var: `max_tokens` tipik bir yerel üretim hızında
`request_timeout_seconds`'tan uzun sürecekse DeerX açılışta uyarır, istekleri
üretimin ortasında kestirmez.

## İkisi arasındaki gerçek farklar

### Sunucu tarafı web araması — yalnızca Anthropic

Anthropic'te `web_search` ve `web_fetch` Anthropic'in altyapısında çalışır.

Onun dışında arama, doğru yapılması en zor kısım; ve dürüst cevap varsayılan
değil ölçülen cevaptır.

#### Ölçülen

DeerX'in dürüst User-Agent'ı (`DeerXAgent/0.1`) ile, gerçek bir Chrome'da:

| Motor | Sonuç |
|---|---|
| Bing | **Otomasyona sahte sonuç kümesi veriyor** — 200 ve tamamen başka bir konunun makul görünen HTML'i |
| DuckDuckGo (html / lite) | CAPTCHA — "select all squares containing a duck" |
| DuckDuckGo (JS) | Sayfa geliyor, sonuçlar hiç render olmuyor |
| Startpage · Mojeek · Brave · Ecosia | Access Denied · 403 · Captcha · challenge |
| Google | Onay sayfasına yönlendiriyor |
| Halka açık SearXNG örnekleri | 429 / 403 (hız sınırı) |

Tehlikeli olan Bing davranışı. `BaseHTTPRequestHandler threading` sorulduğunda
Domino's Pizza Japan ve Google Photos döndürdü — 200 ve normal görünen
biçimlemeyle. Başarısız bir arama ajana "bunu cevap sayma" diye bildirilir;
**sahte** bir arama ise araştırma gibi görünür ve rapora kaynak olarak girer.

DeerX artık bunu tespit ediyor: sonuçların hiçbirinde sorgunun hiçbir terimi
geçmiyorsa küme atılıyor ve "arama çalışmadı" hatası veriliyor.

#### Çalışan çözüm: kendi SearXNG'niz

```bash
docker run -d --name deerx-searxng --restart unless-stopped \n  -p 127.0.0.1:8890:8080 \n  -v /yol/searxng:/etc/searxng:rw \n  searxng/searxng:latest
```

`settings.yml` içinde `search.formats` listesine `json` ekleyin — **varsayılan
olarak kapalıdır** ve olmadan uç 403 döner:

```yaml
use_default_settings: true
server:
  secret_key: "degistirin"
  limiter: false
search:
  formats: [html, json]
```

Sonra:

```toml
[deerx]
search_provider = "searxng"
searxng_url = "http://127.0.0.1:8890"
```

Anahtar yok, engel yok, hız sınırı yok — kendi örneğiniz. Ve SearXNG kapsam
konusunda dürüst: `unresponsive_engines` alanı hangi motorun neden düştüğünü
söylüyor ve DeerX bunu ajana geçiriyor, kapsamın sessizce daralmasına izin
vermiyor.

Bing'i bozan aynı sorgularda ölçülen:

```
os.replace atomic file write windows
  1. os.link() vs os.rename() vs os.replace() for writing atomic write files
     stackoverflow.com/questions/60369291
  2. Extending os.rename() to support file swapping and whiteout
     discuss.python.org/t/22257
```

#### Google, resmî uç üzerinden

Google'ın arama sayfası otomatik tarayıcıyı doğrudan reddediyor — ölçüldü,
gerçek Chrome'la bile: *"Our systems have detected unusual traffic from your
computer network."* Bot korumasını aşmak bu projenin yaptığı bir şey değil, o
yüzden Google yalnızca lisanslı Programmable Search JSON API'siyle geliyor:

```toml
[deerx]
search_provider = "google"
google_cse_id = "..."          # programmablesearchengine.google.com adresinden
```

```bash
# .env
SEARCH_API_KEY=...             # Custom Search API anahtarı
```

**İkisi birden** gerekiyor; yalnızca biriyle uç 400 döner ve mesajı hiçbir şey
anlatmaz, bu yüzden DeerX eksik ayarı adıyla söyler. Ücretsiz katman günde 100
sorgu; bir araştırma fazı birkaç tane kullanır.

Zaten SearXNG çalıştırıyorsanız buna gerek olmayabilir: SearXNG kendi
örneğinizden Google'ı sunucu tarafında sorgulayabilir, anahtar ve kota yok.

#### Anahtarlı alternatif

Konteyner çalıştırmak istemiyorsanız, anahtarlı `brave` veya `tavily`
programatik erişim için lisanslıdır ve engellenmez:

```toml
[deerx]
search_provider = "brave"      # ya da "tavily"
```

```bash
# .env
SEARCH_API_KEY=...
```

Bunlar Ayarlar ekranından da girilebilir ve gerçekten arama yapan bir
**Aramayı test et** düğmesi vardır.

Boş sonuç ajana **hata** olarak bildirilir, asla "sonuç yok" olarak değil.
Başarısız bir aramayı "böyle bir şey yok" diye okuyan bir model bunu rapora
olgu olarak yazar.

`fetch_url` bilinen bir adresi anahtarsız okur ve iki sağlayıcıda da çalışır.
`browse_page` (JavaScript ile üretilen sayfalar için) `browser` ekini ister.

### Prompt önbelleği — yalnızca Anthropic

Anthropic'in prompt önbelleği sistem prefix'ini kapsar. DeerX sistem prompt'unu
**sabit** tutar ve değişken proje durumunu ilk kullanıcı mesajına koyar; tam da
prefix önbelleklenebilir kalsın diye — sistem prompt'undaki değişken içerik
önbelleği her turda geçersiz kılardı.

vLLM'in `--enable-prefix-caching` bayrağı aynı tasarımdan aynı kazancı sağlar.

### Adaptif düşünme — yalnızca Anthropic

`effort` ve adaptif düşünme Anthropic parametreleridir. Yerel modeller bunları
yok sayar; qwen3 gibi bir akıl yürütme modeli kendi parser'ıyla düşünür.

### Akış ve yeniden deneme

OpenAI-uyumlu istemci akış yapar ve araç çağrılarını geldikçe birleştirir.
Ayrıca bir koşuyu zehirleyen bir hata biçimini onarır: bozuk araç çağrısı
argümanı. Bunu geçmişe eklemek — orada her turda yeniden okunup modeli daha da
şaşırtmak — yerine istemci JSON'u önce doğrular ve ayrıştırılamayanı düşürür.

Geçici akış hataları hata yayılmadan önce iki kez yeniden denenir.

## Roller ve modeller

Rollere eşlenen üç model yuvası:

```toml
[deerx]
model_lead   = "claude-opus-5"     # analyze, design, plan, qa, review, live
model_worker = "claude-sonnet-5"   # research, mockup, backend, frontend, staging
model_fast   = "claude-haiku-4-5"  # kısa yardımcı çağrılar
```

Tek bir yerel modelle üçünü de aynı ada ayarlayın.

## Maliyet

Yerel modeller sıfır fiyatlanır. Claude `llm/pricing.py` içindeki tablodan
fiyatlanır ve her faz harcamasını kaydeder.

```toml
[deerx]
cost_limit_usd = 5.0    # 0 = sınırsız
```

Tavan aşıldığında koşu harcamaya devam etmek yerine `BudgetExceeded` ile durur.
Mesaj mevcut toplamı ve sınırı söyler.

## Kesilme

Çıktı tavanında kesilen bir yanıt bitmiş bir yanıttan ayırt edilemiyordu — ajan
çıktıyı yazdığını sanıyor, faz hiçbir şey üretmeden bitiyordu.

İstemci artık `stop_reason` kontrol ediyor. `max_tokens` durumunda ajana
mesajının kesildiği, yazmakta olduğu hiçbir şeyin kaydedilmediği ve baştan
başlamak yerine kaldığı yerden devam etmesi söyleniyor. Arka arkaya iki kesilme
sonrasında, yükseltilecek şey olarak `max_tokens`'ı adıyla anan bir mesajla
vazgeçiyor.

## Ayrıca

- [Yapılandırma](configuration.md) — her ayar ve öncelik sırası
- [Başlangıç](getting-started.md) — ilk koşu
- [Mimari](architecture.md) — sağlayıcı katmanı nasıl yalıtılmış
