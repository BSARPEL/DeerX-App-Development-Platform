# Doğrulama durumu

[← Dokümantasyon](README.md) · [English](../verification.md)

Bu sayfa **gerçekten koşularak** doğrulananları doğrulanmayanlardan ayırır.
Ayrımın kendisi mesele: kod doğru göründüğü için bir şeyin çalıştığı iddiası bir
doğrulama değildir, ve ikisini karıştırmak dürüst iddiaları da değersizleştirir.

## Süit

Python 3.11 ve 3.13'te **1712 test geçiyor**, `ruff` temiz.

Hiçbir test ağ çağrısı ya da gerçek model çağrısı yapmaz. Ajanlar
`tests/conftest.py` içindeki sahte istemciye karşı koşar, yani süit
deterministiktir.

Süre makineye bağlı ve burada duran eski sayı hangi makine olduğunu
söylemiyordu. Bir Windows 11 dizüstünde ölçüldü: `--fast` (süreç başlatan
testler hariç) yaklaşık 160 saniye, tam süit 215-285 saniye. Linux'ta
belirgin biçimde daha hızlı — maliyetin çoğu süreç başlatmak ve Windows
bunda en yavaşı. Çalışırken `--fast`, push'tan önce tamamını koşun.

## Koşularak doğrulananlar

**RAG uçtan uca.** Gerçek bir PDF (`pypdf` ile metin çıkarma), gerçek bir DOCX
(`python-docx`), HTML, Markdown, Türkçe karakterler ve cp1254 yedeği.

**Hibrit arama.** RRF füzyonu ve MMR çeşitlendirmesi, `multilingual-e5-large`
ile Türkçe sorgular üzerinde ölçüldü.

**Ajan döngüsü.** Araç gönderimi, `tool_result` biçimi, paralel araç çağrıları,
`pause_turn`, iterasyon sınırı, iptal, hata yayılımı.

**Şerit yönlendirmesi.** backend/frontend/qa/infra görevlerinin doğru ajana
gittiği.

**Soru kapısı, uçtan uca.** Bloke eden bir soru koşuyu durduruyor, faz hiç
çalışmıyor, cevap ya da atlama sonrası kapı açılıyor ve cevap sonrasında bilgi
tabanında aranabiliyor. Üç yüzeyde de doğrulandı — CLI, web API ve MCP.

**Adım seçimi.** Sırasız seçilen adımlar boru hattı sırasına diziliyor,
tekrarlar düşüyor, 1. adım listeden çıkarılamıyor, bilinmeyen adım reddediliyor.

**Arayüz bütünlüğü.** JS'in aradığı her `#kimlik` HTML'de var, her `data-view`
hedefinin bir bölümü var, HTML ya da JS'te geçen her CSS sınıfı tanımlı. Bir
görünüm taşındığında sessizce kırılan tam olarak bunlardır.

**Tasarım ölçeği.** 1458 render edilmiş metin öğesinin tamamı WCAG AA'yı
geçiyor; palet, punto ölçeği ve boşluk ızgarası sabitlenmiş.

**Bozuk dosya dayanıklılığı.** Geçersiz bir PDF ya da DOCX indekslemeyi
düşürmüyor; hata raporlanıyor ve sağlam dosyalar yine indeksleniyor.

**Dosya yükleme.** Yol geçişi engelleniyor, desteklenmeyen uzantı reddediliyor,
okunamayan dosya çalışma alanında bırakılmıyor — ve aynı adlı çalışan bir
dosyanın üstüne yazıldıysa eskisi geri getiriliyor.

**Teslimat paketleme.** Hazırlık kapısı boş planda, başarısız/bitmemiş görevde
ve bloke eden soruda engelliyor. Gerçek bir çalışma alanında `.env`,
`deploy.pem`, `node_modules/` ve `.git/` dışlanıyor, `.env.example` korunuyor ve
üretilen zip'in ham baytlarında hiçbir sır değeri geçmiyor.

**Web.** HTTP API'nin tamamı, SSE yayıncı döngüsü, onay kapısının koşu iş
parçacığını gerçekten bloke edip cevapla serbest bıraktığı, eşzamanlı koşu
reddi.

**Yerel bir vLLM'e karşı tam bir `ingest → plan` koşusu** (`qwen3.8 max`, 262K
pencere), örnek saha servis şartnamesi üzerinde. Yedi faz, **82 model çağrısı,
3,4M girdi / 329K çıktı token, ücretsiz**, ve her faz sözleşmesini tuttu:

| Faz | Üretilen |
|---|---|
| `ingest` | 11 parça, gerçek 1024 boyutlu `multilingual-e5-large` vektörleri |
| `analyze` | 35 gereksinim, 13 boşluk, 5 soru; `analiz-raporu.md` prompt'unun dayattığı bölüm yapısına birebir uyuyor |
| `research` | 16 bulgu, 12'si kaynak URL'li, her biri güven düzeyi etiketli — gerçek Chrome gezinmesi; sayfa zaman aşımı ve 404 yutuldu, koşu düşmedi |
| `assess` | boşluklar 13 → 27 (1 kritik, 7 yüksek), `bosluk-analizi.md` |
| `mockup` | 6 tek dosyalık ekran, şartnamedeki her aktör için biri; hepsi JavaScript'li, hepsinde boş ve hata durumu |
| `design` | 18 ADR — her birinde gerekçe, alternatif **ve** takas — artı 34 KB'lık `mimari.md`; kararlar araştırma bulgularını sürüm numarasıyla anıyor |
| `plan` | beş şeride bölünmüş 42 görev (backend 21, qa 8, frontend 7, infra 5, docs 1); 42/42'sinde kabul ölçütü ve dosya adı, 41/42'sinde bağımlılık |

İki davranış çıkarımla değil, canlı gözlemle doğrulandı: tur bütçesi uyarısı tam
%70'te tetiklendi (`24/35`), ve hazırlık kapısı paketlemeyi reddedip boş planı,
açık kritik boşlukları ve koşulmamış fazları tek tek saydı.

**Arayüz, render edilmiş hâliyle.** Bütün görünümler, iki dil, iki tema, ayarlar
ekranından canlı model çağrısı (`qwen3.8 max · 2,3 sn · 64 → 43 token`),
tarayıcıdan indeksleme ve hibrit arama, üretilen bir mockup'ın kum havuzlu
çerçevede render'ı, ve hazırlık kapısı. Bir sonraki bölümün doğrulanmamış diye
saydığı madde böylece kapandı.

**Konteyner imajı, uçtan uca.** Deponun `Dockerfile` dosyasından `docker build`,
ardından belgelenen sıra: bağlanmış bir çalışma alanına `deerx user add`,
sunucunun `0.0.0.0` üzerinde başlatılması ve `GET /` isteğine
`{"configured": true, "required": true}` ile 200 dönmesi — yani ilk
konteynerde açılan hesap birim üzerinden ikinciye taşındı. Reddetme yolu da
denendi: hesapsızken `0.0.0.0`a bağlanmak sunucuyu açmak yerine durduruyor.

**İki dillilik.** İki dilin de her anahtarı eşleşen yer tutucularla kapsadığı;
dili değiştirmenin gerçek mesajları değiştirdiği (araç hataları, ajan
yönlendirmeleri, faz adları); `deerx --help` çıktısının **ayrı bir süreçte**
ortam değişkenini izlediği; araç açıklamalarının ve parametrelerinin değişirken
sınıf düzeyindeki şemanın kirletilmediği; ve kaynakta kullanıcıya ya da modele
giden hiçbir metnin sabit kalmadığı.

**MCP.** Araç ve kaynak kaydı, artı gerçek bir alt süreçte JSON-RPC el sıkışması.

**Şema geçişi.** `lane` ve `plan_id` sütunlarından önceki bir veritabanında:
açılış çökmüyor ve planı olmayan görevler ana plana taşınıyor.

**Kesilen koşular.** `running` kalan görevler açılışta kuyruğa dönüyor.
Dönmeselerdi ne kendileri ne de onlara bağlı olanlar tekrar hazır sayılırdı ve
plan kilitlenirdi.

**Yerel vLLM'e karşı tam bir analist koşusu** (Docker'da `vllm/vllm-openai`,
Qwen3, araç çağırma açık): 5 model çağrısı, 57K girdi / 22.8K çıktı token,
**ücretsiz**. Analist şartnameyi baştan sona okudu ve 31 gereksinim (her biri
`§bölüm` dayanağıyla), 12 boşluk (şiddet ve alan etiketli), kullanıcıya 3 soru
(her biri gerekçesi ve önerilen varsayımıyla) ve `analiz-raporu.md` üretti. Araç
çağırma gidiş-dönüşleri, paralel araç çağrısı, akışlı üretim ve yapılandırılmış
kayıt çalıştı.

**Tarayıcıda elle.** Gerçek bir indeksleme koşusuyla canlı SSE, soru panelinden
cevaplama, sürükle-bırak yükleme, markdown ve mockup işleme, hibrit arama, plan
ve analiz görünümleri, klavye gezinmesi.

**Uçtan uca bir geliştirme görevi, aynı yerel vLLM ile.** Tek görev —
"`http.server` ile bir `/health` ucu ve onun için pytest yaz" — `pending`'den
`done`'a geçti: ajan Python ve pytest sürümlerini denetledi, `saglik.py` ve
`test_saglik.py` yazdı, testleri koşturdu. Kendi paketi 7/7 geçti. Sonradan
bağımsız olarak doğrulandı: modülü içe aktarmak sunucuyu başlatmıyor (0.01 sn),
`GET /health` 200 ve `application/json` ile `{"status": "ok"}` dönüyor, başka
her yol 404 dönüyor.

**[Projenin kendi bilgi tabanı](knowledge-base.md), aynı modelle sorgulandı.**
154 doküman, 1.712 parça, CPU üzerinde `multilingual-e5-large` ile 23 dakikada
gömüldü. Üç soru:

| Soru | Sonuç |
|---|---|
| *"denetim günlüğü ne kaydediyor ve neden ayar değerleri yazılmıyor?"* | Doğru ve kaynaklı; `security.md`, `web-ui.md`, `test_auth.py` ve `auth.py`'yi birleştirdi — sebebi dahil: değerlerin arasında API anahtarları var |
| *"`deerx.ps1` neden UTF-8 BOM ile kaydedilmek zorunda?"* | Doğru, bir test docstring'inden — PowerShell 5.1 dosyayı cp1254 sanıyor, bozulan uzun tire bir dizgi açıp dosyayı yutuyor |
| *"DeerX Kubernetes üzerinde nasıl ölçeklendirilir, hangi Helm chart?"* | **"Bu bilgi tabanda yok."** Hiçbir şey uydurulmadı |

Asıl önemli olan üçüncüsü. Uydurulmuş bir cevap yanlış cevaptan pahalıdır:
yanlış olduğunu anlamak için doğrusunu bilmek gerekir.

## Doğrulanmayanlar

**Canlı bir Claude API çağrısı.** Geliştirme ortamında `ANTHROPIC_API_KEY`
yoktu, dolayısıyla `llm/anthropic_client.py` içindeki gerçek istek yolu —
adaptif düşünme, prompt önbelleği — modele karşı sınanmadı. Sözleşme testlerle
kapsanıyor; sözleşmenin diğer ucu API'nin kendisi.

**8–13. fazlar gerçek bir modele karşı.** `implement`, `qa`, `review`,
`package`, `staging` ve `live` yalnızca sahte istemciyle koştu. 1–7. fazlar
artık gerçek bir yerel modelle uçtan uca koşuldu (yukarıda) ve tek bir uygulama
görevi ayrıca doğrulandı, ama burada `plan → live` aralığını raporlayan bir şey
yok: bu şartname üzerinde hiçbir ajan tek bir kesintisiz koşuda kod yazıp kendi
testlerini çalıştırıp sonucunu incelettirmedi.

Dürüst kalan bu. Doğrulanmış olan yarı, **neyin** yapılacağına karar veren yarı;
doğrulanmamış olan ise onu yapan yarı.

## Bilinen düzeltmeler

Sessizce yanlış davranan, bulunup düzeltilen yerler.
`tests/test_regressions.py` her birini koruyor — sessizce yanlış, kalıcı bir
koruma gerektiren kategoridir, çünkü kendini duyuran hiçbir yanı yoktur.

| Sorun | Etkisi |
|---|---|
| `.env` çalışma alanından değil geçerli dizinden okunuyordu | Belgelenen MCP kurulumu API anahtarını sessizce görmezden gelirdi |
| Kabuk zaman aşımı süreç ağacını öldürmüyordu | 2 sn sınırla 30 sn'lik komut 30 sn sürdü |
| `hidden` özniteliği CSS'le eziliyordu | Onay penceresi açılışta tüm arayüzü kapatıyordu |
| Paralel araç çağrılarında tur bütçesi yoktu | 10 araç × 24K = 240K karakter tek turda bağlamı taşırıyordu |
| `@` ile başlayan cevap dosya yolu sanılıyordu | `deerx answer Q-001 "@firma.com…"` çöküyordu |
| `deerx.toml` yazım hatası sessizce yutuluyordu | `aproval_mode` yazınca ayar hiç uygulanmıyordu, uyarı yoktu |
| Vektör önbelleği süreçler arası bayat kalıyordu | CLI'den indekslenen belge web sunucusunun aramasında görünmüyordu |
| Olay günlüğü sınırsız büyüyordu | Uzun koşular diski şişiriyordu |
| Bozuk PDF/DOCX indekslemeyi çökertiyordu | `docs/` içindeki tek bozuk dosya tüm fazı düşürüyordu |
| Kesilmiş yanıt bitmiş yanıt gibi görünüyordu | Ajan çıktıyı yazdığını sanıyordu; faz hiçbir şey üretmiyordu |
| Çok satırlı komut Windows'ta yarım koşuyordu | `cmd.exe` yeni satırı sonlandırıcı sayar: çıkış 0, gerisi düştü |
| Faz çıktısını üretmeden `done` diyebiliyordu | Sonraki fazlar var olmayan bir şeyin üstüne kuruluyordu |
| Reddetme listesi alt dizi eşleştiriyordu | `srv.shutdown()` ve `--shutdown-timeout` `shutdown` komutu sanılıp reddediliyordu |
| Bozuk araç çağrısı argümanları geçmişe giriyordu | Her turda yeniden okunup modeli daha da şaşırtıyordu |
| `enable_web = false` yerel önizlemeyi de kapatıyordu | Ajan az önce yazdığı uygulamayı açamıyordu |
| i18n tarayıcısı liste ifadelerini atlıyordu | İngilizce kurulumda kurulum jetonu başlığı Türkçe kalıyordu |
| `.githooks/pre-push` beş yerde anlatılıyordu ama hiç var olmadı | CI'nın yerine konan denetim hiçbir şey koşmuyordu; git olmayan bir kancayı tek kelime etmeden atlar |
| Kayıt, süreç ağacı öldürülmeden düşürülüyordu | `alive` yalnızca doğrudan çocuğa bakar; ölen ara kabuk asıl sunucuyu yetim bırakıyordu — bir çalışma alanında 115 tanesi birikmiş, her biri bir portu tutuyordu |
| Kurulum yoklaması var olmayan `Embedder.encode`'u çağırıyordu | `setup --with-embedding-model` hiçbir şey indirmiyordu; `AttributeError` yutulup indirme hatası olarak gösteriliyordu |
| Anthropic istemcisi `ToolOutcome.images`'ı yok sayıyordu | `provider = "anthropic"` ile model ekran görüntüsünü hiç görmüyordu — yalnızca "kaydedildi" metnini. Öne çıkan yetenek, bütün modelleri gören sağlayıcıda yoktu |
| Girdi tahmini base64 görsel baytlarını metin sayıyordu | 1 MB'lik bir ekran görüntüsü 559.816 token tahmin ettiriyordu, gerçek maliyeti ~1.600; 262K pencerede ajanın *ilk* ekran görüntüsü koşuyu `context_overflow` ile öldürüyordu |
| Görseller geçmişten hiç kırpılmıyordu | İki kırpıcı da onlara dokunmuyordu; her ekran görüntüsü koşunun sonuna kadar her turda yeniden gönderiliyordu |
| Kabuk politikasında yeni satır ayraç sayılmıyordu | Çok satırlı bir komutun yalnızca ilk satırı denetleniyor, bash hepsini çalıştırıyordu: tek başına reddedilen `whoami`, izinli bir satırın ardına konunca çalıştı. `approval_mode = "auto"` kipinde izin listesi tek bariyerdi |
| Vekil, denetimden sonra adı yeniden çözüyordu | Doğrulanan adresler atılıyor ve `create_connection` adı baştan çözüyordu — DNS rebinding'in kullandığı ikinci çözümleme tam olarak buydu |
| Stilsiz bir DOCX paragrafı bütün dosyayı düşürüyordu | `para.style` None olabiliyor; tek bir stilsiz paragraf yüzünden şartnamenin tamamı indekslenemiyordu |

## Yeniden üretme

```bash
uv sync --extra dev
uv run pytest -q
uv run ruff check src tests
```

Bu belgedeki sayılar için:

```bash
uv run pytest -q --collect-only | tail -1        # test sayısı
uv run deerx doctor                              # ortam
```
