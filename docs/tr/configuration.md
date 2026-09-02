# Yapılandırma

[← Dokümantasyon](README.md) · [English](../configuration.md)

## Öncelik sırası

```
varsayılanlar  <  deerx.toml  <  ortam değişkenleri (DEERX_*)  <  CLI bayrakları
```

`deerx init` her seçeneği satır içinde belgelenmiş bir `deerx.toml` yazar.
Sırlar yanındaki `.env` dosyasına aittir, asla TOML'a değil.

**Taninmayan anahtarlar yutulmaz.** `aproval_mode` gibi bir yazım hatası;
dosyayı, anahtarı ve en yakın gerçek anahtarı adıyla anan bir uyarı üretir —
sessizce hiç uygulanmayan bir ayar, bir daha yaşanmaması gereken bir hataydı.

## `[deerx]` — çekirdek

| Anahtar | Varsayılan | Ne yapar |
|---|---|---|
| `provider` | `"openai"` | `openai` (OpenAI-uyumlu her uç) veya `anthropic` |
| `openai_base_url` | `http://127.0.0.1:8008/v1` | Uç adresi. Docker'da **host** portunu yazın. |
| `model_lead` | `"qwen3.8 max"` | analyze, design, plan, qa, review, live |
| `model_worker` | `"qwen3.8 max"` | research, mockup, backend, frontend, staging |
| `model_fast` | `"qwen3.8 max"` | kısa yardımcı çağrılar |
| `effort_lead` · `effort_worker` | `"high"` | Yalnızca Anthropic; yerel modeller yok sayar |
| `temperature` | *(ucun varsayılanı)* | Sunucunun kendi değerini kullanmak için tanımsız bırakın |
| `request_timeout_seconds` | `1800` | Yerel bir model tek yanıt için dakikalar alabilir |
| `context_window` | *(otomatik)* | Uç `max_model_len` bildirmiyorsa elle verin |
| `max_tokens` | `8000` | Tur başına çıktı tavanı |
| `max_iterations` | `40` | Ajan başına tur tavanı, rol bütçesiyle kırpılır |
| `language` | `"tr"` | `tr` veya `en` — bkz. [İki dilli mimari](i18n.md) |
| `approval_mode` | `"ask"` | `auto`, `ask` veya `dry-run` |
| `enable_web` | `true` | Araştırma için internet erişimi |
| `search_provider` | `"browser"` | `browser`, `duckduckgo`, `searxng`, `google`, `brave`, `tavily` |
| `searxng_url` | `"http://127.0.0.1:8890"` | Kendi örneğiniz; `deerx setup` kurabilir |
| `google_cse_id` | — | Programmable Search motor kimliği (`cx`); Google ayrıca `search_api_key` ister |
| `cost_limit_usd` | `0` | `0` = sınırsız; aşılırsa koşu durur |
| `log_level` | `"INFO"` | |

### `max_tokens` ve zaman aşımı

Bu ikisi etkileşir. Yerel bir akıl yürütme modeli kabaca saniyede 70 token
üretir, yani `max_tokens = 32000` tek bir turu yaklaşık yedi dakikaya çıkarır.
`request_timeout_seconds` bundan küçükse istekler üretimin ortasında kesilir ve
hata bir model sorunu gibi görünür.

DeerX ikisini açılışta karşılaştırır ve çeliştiklerinde uyarır.

Varsayılan `8000` düşünmeye yer bırakırken turu makul uzunlukta tutar. Modelin
düşüncesi kesiliyorsa yükseltin — ajan size söyleyecektir, çünkü kesilmiş bir
yanıt tespit edilip bildiriliyor, bitmiş bir yanıtla karıştırılmıyor.

### `approval_mode`

| Değer | Davranış |
|---|---|
| `ask` *(varsayılan)* | Her dosya yazma ve komut önce size gösterilir |
| `auto` | Hiçbir şey sorulmaz — otomasyon ve gözetimsiz koşular için |
| `dry-run` | Yazmalar uygulanmaz, raporlanır |

`deerx run --yes` tek bir koşu için `auto` ayarlar.

## `[deerx.rag]` — bilgi tabanı

| Anahtar | Varsayılan | Not |
|---|---|---|
| `embedding_model` | `intfloat/multilingual-e5-large` | dim 1024, ~2,2 GB |
| `embedding_dim` | `1024` | **Modelle eşleşmek zorunda** |
| `embedding_provider` | `"fastembed"` | `"hash"` = çevrimdışı test, zayıf getirme |
| `chunk_tokens` | `700` | |
| `chunk_overlap_tokens` | `100` | |
| `top_k` | `8` | Arama başına sonuç |

Daha küçük alternatifler:

| Model | dim | Boyut |
|---|--:|---|
| `sentence-transformers/paraphrase-multilingual-mpnet-base-v2` | 768 | ~1,0 GB |
| `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | 384 | ~0,2 GB |

**Modeli değiştirmek yeniden indeksleme demektir.** `embedding_dim` değerini de
aynı anda güncelleyin ve `deerx ingest --force` çalıştırın. Unutursanız DeerX
sessizce yanlış sonuç döndürmek yerine aramayı reddeder — farklı boyuttaki
vektörler karşılaştırılamaz ve sessiz bir boş sonuç kümesi bir hatadan kötüdür.

## `[deerx.shell]` — komut politikası

| Anahtar | Varsayılan |
|---|---|
| `enabled` | `true` |
| `timeout_seconds` | `300` |
| `allow_prefixes` | `git`, `python`, `uv`, `pip`, `pytest`, `ruff`, `mypy`, `node`, `npm`, `npx`, `pnpm`, `yarn`, `tsc`, `jest`, `vitest`, `ls`, `cat`, `head`, `tail`, `grep`, `find`, `wc`, `echo`, `mkdir`, `docker`, `make`, `go`, `cargo` |

Boş bir `allow_prefixes = []` yalnızca reddetme listesinin uygulanması demektir
— açıkça yıkıcı olmayan her komut serbest kalır. Bunu yapmadan önce
[Güvenlik modeli](security.md) okuyun.

## `[deerx]` — yalıtılmış çalıştırma

Bunların hepsi web arayüzünde de var: **Ayarlar → Yalıtım**. Oradaki
değişiklikler oturuma özeldir ve konteyneri yeniden kurar; kalıcı olmaları
için buraya yazın.

Varsayılan olarak ajanın `run_command` ve `start_service` çağrıları **bu
makinede** koşar ve kabuk izin listesiyle çevrilidir. `execution = "docker"`
derseniz ikisi de tek kullanımlık bir konteynerde koşar; izin listesi o zaman
uygulanmaz, çünkü korunacak konak yoktur ve konteyner koşu bitince silinir.

| Anahtar | Varsayılan | Not |
|---|---|---|
| `execution` | `"host"` | `host` ya da `docker` |
| `sandbox_image` | `"python:3.13"` | `-slim` değil: ölçüldü, slim içinde `git`, `curl`, `gcc`, `make` yok |
| `sandbox_setup` | `""` | Konteyner ilk kurulduğunda bir kez koşar, ör. `apt-get update && apt-get install -y nodejs` |
| `sandbox_port_base` | `8100` | Yayınlanan ilk port |
| `sandbox_port_count` | `10` | Kaç port yayınlanır |
| `sandbox_memory` | `"2g"` | |
| `sandbox_cpus` | `2.0` | |
| `sandbox_pids` | `512` | |

Portlar konteyner kurulurken `127.0.0.1`'e yayınlanır; servis o aralıktan bir
port seçmeli ve içeride `0.0.0.0`'a bağlanmalıdır. Docker yayınlanan portu
sonradan ekleyemez. Kaynak sınırları süs değil: onlar olmadan bir fork bombası
ya da bellek doldurma konteynerde kalmazdı.

Neyin yalıtıldığı ve neyin yalıtılmadığı için [Güvenlik](security.md) — özellikle
şu: çalışma alanı bağlanır, yani makine korunur ama proje korunmaz.

## Ortam değişkenleri

Her `[deerx]` anahtarı büyük harfle `DEERX_<ANAHTAR>` olarak verilebilir.
Çalışma alanındaki `.env` dosyasından okunanlar:

| Değişken | Ne için |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic |
| `OPENAI_API_KEY` | Anahtar isteyen OpenAI-uyumlu uçlar |
| `DEERX_OPENAI_BASE_URL` | Uç adresi |
| `SEARCH_API_KEY` | Brave veya Tavily |
| `DEERX_WORKSPACE` | Hangi çalışma alanı — bulunduğunuz dizinden bağımsız |
| `DEERX_LANGUAGE` | Tek bir çağrı için `tr` ya da `en` |

**`.env` geçerli dizinden değil, çalışma alanından okunur.** Aksi halde
`deerx serve --workspace X` ya da `DEERX_WORKSPACE` ile başlatılan bir MCP
sunucusu projenin kendi anahtarını sessizce görmezden gelirdi. İkisi de varsa
çalışma alanı kazanır.

### Tek bir çalışma alanına sabitlemek

Çalışma alanı çözümü `deerx.toml` ararken **yukarı** doğru yürür; yani bir üst
dizinden çalıştırılan komut, altındaki bir çalışma alanını hiçbir zaman bulmaz.
Nerede olursanız olun tek bir alanın geçerli olması için:

```bash
export DEERX_WORKSPACE=/srv/projeler/musteri-x
```

```powershell
setx DEERX_WORKSPACE D:\projeler\musteri-x
```

Açıkça verilen `--workspace` yine kazanır — bayrak, ortamdan daha belirli bir
niyettir. Dizin olmayan bir değer sessizce **kabul edilmez**: uyarır ve
bulunulan dizine düşer, çünkü bir yazım hatası aksi halde komutlarınızın
sessizce bambaşka bir yerde çalışması demek olurdu.

Yönetim betiklerinin kendi karşılığı var:
[`scripts/deerx.local.conf`](cli.md) — portu ve adresi de sabitler.

### `DEERX_LANGUAGE` dosyayı ezer

Dil, ortam değişkeninin `deerx.toml`'u yendiği tek ayardır — diğer her ayarın
tersi. Sebebi, CLI yardımının yalnızca ortam değişkenini görebilmesi. Dosya
çalışma anında kazansaydı `DEERX_LANGUAGE=en deerx init` İngilizce yardım ve
Türkçe panel basardı — iki dilden de kötü.

Koddaki açık bir override yine ortam değişkenini yener:

```
overrides  >  DEERX_LANGUAGE  >  deerx.toml  >  "tr"
```

## Prompt'ları ezmek

Bir rolün paket içindeki yönergesini koda dokunmadan değiştirmek için çalışma
alanınızda `prompts/<rol>.md` oluşturun. Arama sırası:

```
calisma-alani/prompts/<rol>.md   →   paket prompts/<dil>/<rol>.md   →   paket prompts/<rol>.md
```

Roller: `analyst`, `researcher`, `assessor`, `mockup`, `architect`, `planner`,
`backend`, `frontend`, `qa`, `reviewer`, `staging`, `live`, ve hepsinin önüne
eklenen `_shared`.

## Web arayüzündeki ayarlar

Ayarlar ekranı bunların çoğunu canlı düzenler. Bilinmesi gereken iki şey:

- **API anahtarları geri dönmez.** Ayarları okumak yalnızca bir anahtarın
  tanımlı olup olmadığını döner, değerini asla.
- **Değişiklikler oturum içindir.** Kalıcı olması için `deerx.toml`'a yazın.
  Koşu sürerken model ayarı değiştirilemez ve bir tanesini değiştirmek LLM
  istemcisini düşürür — aksi halde istemci bu değerleri kurulumda okuduğu için
  değişiklik sunucu yeniden başlatılana kadar sessizce etkisiz kalırdı. Yalıtım
  ayarları da aynı şekilde davranır ve konteyneri yeniden kurar: Docker
  yayınlanan portları ve kaynak sınırlarını konteyner yaratılırken ayırır.

## Ayrıca

- [Model sağlayıcıları](providers.md) — uç kurulumu ve farklar
- [Güvenlik modeli](security.md) — kabuk politikası gerçekte neyi garanti ediyor
- [Web arayüzü](web-ui.md) — Ayarlar ekranı
