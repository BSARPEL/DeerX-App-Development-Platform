# Teslimat paketleri

[← Dokümantasyon](README.md) · [English](../delivery.md)

Uygulama bittiğinde DeerX işi **tek bir zip** olarak teslim eder — ama önce
teslim edilebilir olduğunu doğrular. Yarım kalmış bir işi paketlemek
paketlememekten kötüdür, çünkü bitmiş gibi görünür.

```bash
uv run deerx package
```

Faz olarak da koşar (`deerx phase package`) ve model gerektirmez: tamamı
deterministiktir.

## Hazırlık kapısı

Şunlardan biri varsa paketleme **reddedilir** (çıkış kodu `2`):

| Engel | Neden |
|---|---|
| Plan boş | Paketlenecek tanımlı bir iş yok |
| Başarısız görev var | Bozuk kod teslim edilmez |
| Bitmemiş görev var | Eksik iş "tamam" diye gönderilmez |
| Cevaplanmamış bloke eden soru | Yanlış bir varsayım koda çoktan girmiş olabilir |

Şunlar **uyarı** olarak bildirilir ama durdurmaz: açık kritik veya yüksek
boşluklar, ve hiç koşmamış bir QA ya da inceleme fazı.

Yine de paketlemek için:

```bash
uv run deerx package --force
```

Uyarılar ve engeller o zaman manifestoya yazılır, yani teslim edilen arşiv
temiz görünmek yerine kendi çekincelerini taşır.

**Zorlama yalnızca bu bayrakla olur.** `deerx run --force` tamamen başka bir şey
demektir — "tamamlanmış bir fazı yeniden koş" — ve teslimat kapısını açmaz. Bir
koşuyu baştan almak isteyen kullanıcı yarım bir projeyi göndermeyi istemiş
olmaz, ve tek bir bayrak adının iki işi birden yapması eninde sonunda
gönderilmemesi gereken bir şeyin gönderilme sebebi olurdu.

## Sır dışlama

Şunlar arşive asla girmez:

```
.env  *.pem  *.key  id_rsa*  credentials*  service-account*.json  ...
```

`.env.example` gibi şablonlar korunur — onlar sır değil, belgedir.

Ayrıca dışlananlar: `.git/`, `.deerx/`, `node_modules/`, `.venv/`,
`__pycache__/`, derleme çıktıları ve önbellekler. Bu desenler yolun **her**
parçasına uygulanır, yani bir monorepo'daki `frontend/node_modules/` kökteki
kadar dışlanır.

Dışlanan her sır dosyası manifestoda `DAHIL EDILMEDI` olarak listelenir.
Dışlama sessiz değil görünürdür — manifestoyu okuyan biri bir dosyanın neden
eksik olduğunu merak etmek yerine bilerek tutulduğunu görür.

Bir testin bütün işi, üretilen zip'in ham baytlarında hiçbir sır değerinin
geçmediğini doğrulamaktır.

## Arşiv düzeni

```
<proje>-20260828-1430.zip
└── <proje>/
    ├── TESLIMAT.md        kapsam, gereksinim izlemesi, görevler, dışlananlar
    ├── README.md          projenin kendi dosyaları
    ├── src/ ...
    ├── tests/ ...
    └── belgeler/          analiz, mimari, plan, QA ve inceleme raporları
```

Paket `.deerx/teslimat/` altına yazılır ve proje hafızasına `package` türünde
bir çıktı olarak kaydedilir.

Önceki teslimat zip'leri yeni pakete asla alınmaz. Alınsaydı her paket bir
öncekini sarmalar ve boyut her sürümde katlanırdı.

## `TESLIMAT.md`

Manifesto, teslimatın kendisi hakkındaki beyanıdır:

```
Durum · Sayılar · Neler yapıldı (faz faz, her ajanın özetiyle) ·
Karşılanan gereksinimler · Tamamlanan görevler · Mimari kararlar ·
Belgeler · Paket içeriği · Pakete alınmayanlar · Açık konular
```

"Pakete alınmayanlar" ve "açık konular" oraya bilerek konuldu. Kendi
boşluklarını gizleyen bir teslimat, bu kapının var olma sebebi olan hata
biçiminin ta kendisidir.

## Arayüzde

Zip metin değil, **ek dosyadır**. Bir arşivin ham baytlarını ekrana dökmek bir
ekran dolusu çöp üretir; onun yerine paket indirilebilir bir kart olarak durur
ve altında `TESLIMAT.md` rapor olarak işlenir.

```
🗜  <proje>-20260828-1430.zip
    teslimat arşivi · 12.1 KB · 24 dosya          [ İndir ]

    ▸ İçindekiler — 24 dosya          (katlanmış)

    BU PAKET İÇİN NE YAPILDI
    ────────────────────────
    Durum · Sayılar · Neler yapıldı · Karşılanan gereksinimler · ...
```

Aynı kural `.png`, `.pdf` ve diğer ikili çıktılar için de geçerlidir. MCP
tarafında `deerx_artifact <paket.zip>` bayt yerine bu raporu döner.

Elle paketleme tek adımlı bir koşu kaydı oluşturur — o olmadan paket hiçbir
koşuya ait olmaz ve Koşular görünümünden erişilemezdi.

## Ayrıca

- [Boru hattı](pipeline.md) — bağlam içinde 11. faz
- [Güvenlik modeli](security.md) — sır desenleri nasıl uygulanıyor
- [CLI referansı](cli.md) — bayraklar ve çıkış kodları
