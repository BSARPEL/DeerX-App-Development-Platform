# Mimari

[← Dokümantasyon](README.md) · [English](../architecture.md)

## Modül haritası

```
src/deerx/
├── config.py            deerx.toml + .env birleşimi, rol→model eşlemesi
├── i18n.py              Türkçe/İngilizce mesaj kataloğu (Python tarafı)
├── errors.py            istisna hiyerarşisi
├── logging.py           olay günlüğü, konsol, glifler
├── process.py           süreç ağacı öldürme, spawn bayrakları, alt süreç ortamı
├── sandbox.py           ajanin komutlarinin kostugu istege bagli konteyner
├── services.py          koşuya bağlı arka plan süreçleri
│
├── llm/                 sağlayıcıdan bağımsız model katmanı
│   ├── base.py            LLMClient sözleşmesi, nötr tipler, kullanım defteri
│   ├── anthropic_client.py  adaptif düşünme, prompt önbelleği, içerik blokları
│   ├── openai_client.py     vLLM/Ollama/OpenAI; akışlı araç çağrısı birleştirme
│   ├── providers.py         her protokolü konuşan bilinen servisler ve uçları
│   └── pricing.py           yerel modeller ücretsiz, Claude fiyatlanır
│
├── rag/                 bilgi tabanı
│   ├── loaders.py         PDF / DOCX / HTML / Markdown / kod
│   ├── chunker.py         başlık ve kod sınırlarına duyarlı parçalama
│   ├── embedder.py        yerel ONNX gömme (fastembed) + çevrimdışı yedek
│   ├── store.py           SQLite + FTS5 + numpy kosinüs
│   ├── retriever.py       RRF füzyonu + MMR çeşitlendirme
│   └── knowledge.py       tek giriş noktası
│
├── tools/               39 ajan aracı
│   ├── base.py            Tool sözleşmesi, defter, onay kapısı, yol hapsi
│   ├── filesystem.py      çalışma alanına hapsedilmiş oku/yaz/düzenle/ara
│   ├── shell.py           reddetme listesi + izin listesi + onay
│   ├── services.py        start_service, service_log, stop_service, list_services
│   ├── knowledge.py       search_knowledge, read_document, ingest_source
│   ├── browser.py         preview_open, browser_snapshot/_click/_type/_console
│   ├── web.py             fetch_url (kalıcı indeksleme), browse_page
│   ├── images.py          find_images / download_image, lisans farkindaligiyla
│   ├── project.py         record_*, save_artifact, read_project_state
│   └── descriptions_en.py araç açıklamalarının İngilizce tarafı
│
├── agents/              12 rol ajanı
│   ├── base.py            düşün → araç → gözlemle döngüsü, kırpma, iptal
│   ├── roles.py           rol → araç kümesi + sunucu araçları + iterasyon bütçesi
│   ├── prompts.py         çalışma alanı ve dil ezmeleriyle prompt yükleme
│   └── prompts/           13 prompt (markdown) + prompts/en/
│
├── pipeline/
│   ├── models.py          13 faz, Requirement, Question, Gap, Decision, Task, Artifact
│   ├── state.py           SQLite proje hafızası + sürüm geçişi
│   ├── packaging.py       hazırlık kapısı, sır dışlama, teslimat arşivi
│   └── orchestrator.py    faz durum makinesi, şerit yönlendirmesi, soru kapısı
│
├── browser/
│   ├── session.py         Playwright ile gerçek Chrome, tembel başlatma
│   ├── proxy.py           filtreleyen vekil (CONNECT + absolute-form)
│   └── policy.py          DNS-rebinding savunmalı URL politikası
│
├── web/
│   ├── app.py             Starlette JSON API + SSE
│   ├── auth.py            kullanıcılar, oturumlar, scrypt, kilitlenme
│   ├── runner.py          arka plan koşusu, olay tamponu, onay kapısı
│   └── static/            index.html + styles.css + app.js + i18n.js
│
├── mcp_server/server.py MCP arayüzü
└── cli.py               Typer CLI
```

## Neden bu tasarım

### Yapısal çıktı, serbest metin değil

Ajanlar bulgularını, bir ayrıştırıcının yorumlamak zorunda kalacağı düzyazı
yerine `record_requirements` gibi araçlarla kaydeder. Çıktı sorgulanabilir,
kalıcı ve fazlar arası devredilebilir olur — LLM yanıtından JSON çıkarmaya
çalışıp bunun her başarısızlık biçimini ele almak yerine.

### Sağlayıcı sızıntısı yok

Ajan döngüsü hiçbir sağlayıcının mesaj biçimini bilmez. Konuşma geçmişine
yalnızca istemci dokunur (`append_assistant`, `append_tool_results`,
`trim_history`); Anthropic'in içerik blokları ile OpenAI'nin `tool_calls` biçimi
arasındaki fark tek bir dosyada kalır.

`LLMClient` bir `Protocol` ve bunun bilinmesi gereken keskin bir kenarı var:
gövdeler çalışma anında **devralınmaz**. Protokole eklenip yalnızca bir
istemcide uygulanan bir metot, diğer sağlayıcıyı bekleyen bir çökmedir — bu
yaşandı ve testler kaçırdı, çünkü sahte istemciye metot elle eklenmişti. Artık
her protokol metodunun her somut istemcinin kendi `__dict__`'inde bulunduğunu
doğrulayan bir sözleşme testi var.

### Soru kapısı fazın öncesinde yoklanır, sırasında değil

Cevapsız bloke eden bir soruyla faza girmek, ajanın yanlış olabilecek bir öncül
üzerinde çalışması ve o işin çöpe gitmesi demektir. Kontrolün maliyeti yok;
fazın maliyeti bir model koşusu.

Cevap proje hafızasına **ve** bilgi tabanına yazılır. Uzun bir koşuda geçmiş
kırpılır ve yalnızca geçmişte yaşayan bir cevap sessizce var olmaktan çıkardı.

### Uzmanlaşmış ajanlar, dar araç kümeleri

Backend ajanının tarayıcısı yok; Canlı ajanı dosya yazamaz. Dar bir araç kümesi
token maliyetini düşürür ve bütün bir yanlış-araç seçimi sınıfını ortadan
kaldırır. [Ajan araçları](tools.md) içindeki tabloya bakın — yokluklar tasarımın
kendisi.

### Hibrit arama

Anlamsal arama eşanlamlıları, BM25 özel isimleri ve kod tanımlayıcılarını
yakalar. Skorları karşılaştırılabilir olmadığı için **sıra** bazlı (RRF)
birleştirilir. MMR çeşitlendirmesi sonra *füzyon skorunu* alaka terimi olarak
kullanır — o noktada yeniden kosinüs hesaplamak sözcüksel katkıyı tamamen
atardı.

### Sunucusuz depo

SQLite + FTS5 + numpy. Proje ölçeğindeki korpuslarda kaba kuvvet kosinüs araması
milisaniyeler sürer; harici bir vektör veritabanı karşılığı olmayan bir
bağımlılıktır.

Vektör önbelleği süreçler arası geçersizleştirilir, çünkü web sunucusu açıkken
CLI'den indekslenen bir doküman anlamsal aramada görünmüyordu.

### Sabit sistem prompt'u

Değişken proje durumu ilk kullanıcı mesajına gider, asla sistem prompt'una.
Prompt önbelleği sistem prefix'ini kapsar ve oradaki değişken içerik onu her
turda geçersiz kılardı. vLLM'in prefix önbelleği de aynı biçimden yararlanır.

### İşbirlikçi iptal

"Durdur" bir bayrak kaldırır ve ajan tur sınırında durur. Model çağrısının
ortasında kesmek konuşma geçmişini tutarsız bırakırdı — sonucu olmayan bir araç
çağrısı, sonraki turun kurtarabileceği bir durum değildir.

### Harness bildiğini modele söyler

Bu kod tabanında tekrarlayan bir tema: harness modelin bilmediği bir şeyi bilir
ve bunu söylememek emin ama yanlış bir cevap üretir.

| Harness'ın bildiği | Modele eskiden söylenmeyen | Şimdi |
|---|---|---|
| Yanıt `max_tokens`'a takıldı | Yazmayı bitirdiğini sanıyordu | Kesildiği ve baştan değil kaldığı yerden devam etmesi söyleniyor |
| Turlar tükenmek üzere | Son turlarını araştırmaya harcıyordu | %70'te uyarılıyor: önce çıktıyı kaydet |
| Çok satırlı komut yarım koştu | Çıkış kodu 0, gerisi sessizce düştü | Betiğe yazılıp POSIX kabuğuyla çalıştırılıyor |
| Faz çıktı üretmedi | `done` bildiriyordu | Yönlendiriliyor, bir kez daha deneniyor, sonra başarısız |
| Araç çağrısı argümanları bozuk JSON'du | Geçmişe ekleniyor, her turda okunuyordu | Geçmişe girmeden doğrulanıp düşürülüyor |

### Web katmanı Starlette üzerinde

`starlette`, `uvicorn`, `sse-starlette` ve `markdown-it-py` zaten bağımlılık
ağacındaydı. Birkaç düzine rota için FastAPI katmanı, karşılığı olmayan bir
ağırlıktır. Statik dosyalar `no-cache` ile servis edilir — bir yükseltmeden
sonra önbellekteki `app.js` konuştuğu API ile uyumsuz kalırdı.

### Tek katalog, iki dil

Arayüz metni istemcide çözülür (`static/i18n.js`); sunucudan gelen her şey
`deerx/i18n.py` üzerinden. İkisi faz etiketleri için aynı anahtar adlarını
paylaşır ve bir test aynı fazları kapsadıklarını doğrular. Bkz.
[İki dilli mimari](i18n.md).

## Veri modeli

`.deerx/deerx.db` içindeki proje hafızası:

| Tablo | Ne tutuyor |
|---|---|
| `requirements` | `REQ-nnn`, dokümana geri işaret eden `source_ref` ile |
| `gaps` | `GAP-nnn`, şiddet, alan, dayanak, öneri |
| `decisions` | `ADR-nnn`, alternatifler ve ödünleşmeler |
| `research_notes` | Kaynak URL'si ve güven düzeyiyle bulgular |
| `questions` | `Q-nnn`, bloke bayrağı, cevap ya da varsayım |
| `tasks` | `T-nnn`, şerit, bağımlılıklar, dosyalar, kabul ölçütü, plan |
| `plans` | Adlandırılmış görev grupları, biri etkin |
| `artifacts` | Ad, tür, yol, özet, üreten faz ve koşu |
| `phases` · `runs` · `run_steps` | Faz durumu, koşu geçmişi, adım ayrıntısı |

Şema değişiklikleri açılışta geçirilir. `lane` ve `plan_id` sütunlarından önceki
bir veritabanı çökmeden açılır ve planı olmayan görevler ana plana taşınır —
alternatifi, her mevcut projenin yükseltmede kırılması.

## Test

Süite hiçbir ağ çağrısı ve hiçbir gerçek model çağrısı yapmaz:
ajanlar `tests/conftest.py` içindeki sahte istemciye karşı koşar.

Üç dosyanın alışılmadık işleri var:

- `test_regressions.py` — bir zamanlar sessizce yayımlanmış her hata için bir
  test.
- `test_no_hardcoded_turkish.py` — kaynak üzerinde bir AST yürüyüşü; kullanıcıya
  ya da modele giden bir metin kataloğu atlarsa düşer. **Kendini de** test eder,
  çünkü desenleri silinmiş bir tarayıcı her dosyayı temiz bildirirdi.
- `test_scripts.py` — yönetim betikleri, yokladıkları her yolun `PUBLIC_PATHS`
  içinde olması dahil.

## Ayrıca

- [Boru hattı](pipeline.md) · [Ajan araçları](tools.md) · [Güvenlik modeli](security.md)
- [Doğrulama durumu](verification.md) — gerçekten koşularak doğrulananlar
