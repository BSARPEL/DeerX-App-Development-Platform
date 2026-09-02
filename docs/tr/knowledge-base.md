# Projenin kendi bilgi tabanı

[← Belgeler](README.md) · [English](../knowledge-base.md)

DeerX bir erişim (retrieval) motoru taşıyor. Bu sayfa onu **DeerX'in kendisine**
doğrultuyor: 7.500 satır belge, 16.000 satır kaynak kod ve test paketi —
indekslenmiş, aranabilir, ve bir modelin okuyabileceği halde.

Neden gerekli: "bu neden böyle yapılmış" sorusunun cevabı çoğu zaman düz
anlatımda değil, bir yorumda ya da bir test docstring'inde duruyor. Grep
sözcüğü bulur; erişim pasajı bulur.

## Kurun

```bash
uv run python scripts/knowledge/build.py
```

Yaklaşık bir dakikalık indeksleme, artı gömme süresi — varsayılan
`multilingual-e5-large` ile CPU üzerinde birkaç dakika. Sonuç `.deerx-kb/`
altına düşer (sürüm kontrolüne girmez).

```bash
uv run python scripts/knowledge/build.py --hizli
```

`--hizli` gömme modelini atlar: sözcüksel arama tam çalışır, anlamsal arama
zayıftır. Denemek için iyi, gerçek kullanım için değil.

Diğer bayraklar: başka bir yere kurmak için `--hedef <yol>`, değişmemiş
dosyaları da yeniden indekslemek için `--force`.

### Ne indeksleniyor, neden

| Yol | Neden yerini hak ediyor |
|---|---|
| `README*.md` | Giriş noktası |
| `docs/` | Anlatımın kendisi, iki dilde |
| `src/deerx/` | Kod — yorumları **kararları** anlatıyor, "neden" sorusu genelde orada yaşıyor |
| `tests/` | Depodaki en iyi belge: her test gerçek bir hatanın karşılığı ve docstring'i hangisi olduğunu söylüyor |
| `scripts/` | Kurulum, başlatıcılar, ekran görüntüsü araçları |
| `examples/` | Örnek şartname |

Liste **açık**, depo geneli bir tarama değil. İçinde ne olduğunu kimsenin
sayamadığı bir bilgi tabanı, kimsenin güvenemeyeceği bir bilgi tabanıdır.

Üç dosya adıyla dışarıda — tahminle değil, ölçülerek:

- `static/index.html` — işaretlemesi soyulunca sözcük çorbasına dönüyor.
  *"denetim günlüğü"* sorgusunda `## Denetim günlüğü Kullanıcı İşlem Satır 50
  200 1000 Yenile` diyerek birinci sıraya çıkıyordu; denetim günlüğünü
  gerçekten anlatan `docs/security.md` ise listeye hiç giremiyordu.
- `static/i18n.js` — 1.400 satır anahtar-değer. Her sorguya biraz benziyor,
  hiçbirini cevaplamıyor.
- `docs/images/` — ikili.

## Soru sorun

```bash
uv run python scripts/knowledge/ask.py "denetim gunlugu ne kaydediyor"
```

Üç adım: hibrit arama (anlamsal + sözcüksel, RRF ile birleştirilmiş), bulunan
pasajların **kaynaklarıyla birlikte** tek bir bağlama dizilmesi, ve modele
"yalnızca bunlardan cevapla" denmesi.

Üç kural bilinçli:

- **Yalnızca alıntılardan.** Bir belge tabanının değeri, cevabın nereden
  geldiğini gösterebilmesinde; "bildiğim kadarıyla" diyen bir cevap, tabanın
  hiç sorgulanmamasıyla aynı.
- **Bulunamayan söylenir.** Uydurulmuş bir cevap yanlış cevaptan pahalıdır:
  yanlış olduğunu anlamak için doğrusunu bilmek gerekir.
- **Kaynaklar cevabın altında ayrıca listelenir**, modelin atıf yapıp
  yapmamasından bağımsız olarak — nereye bakacağınızı hep bilirsiniz.

İşe yarayan bayraklar:

```bash
uv run python scripts/knowledge/ask.py "sandbox" --sadece-arama   # yalnızca arama
uv run python scripts/knowledge/ask.py "sandbox" --ayar ./demo    # model ayarları oradan
uv run python scripts/knowledge/ask.py "sandbox" -k 12            # daha çok pasaj
```

`--ayar` önemli: bilgi tabanı bir model ucu tanımlamıyor, tanımlaması da
gerekmiyor. Zaten ucu olan herhangi bir çalışma alanını gösterin.

## Modelsiz sorgulama

Bilgi tabanı sıradan bir DeerX çalışma alanı, yani CLI üzerinde çalışır:

```bash
cd .deerx-kb && uv run deerx search "sandbox nasil calisiyor"
```

`--full` alıntı yerine parçanın tamamını basar, `--kind doc` yalnızca
belgelerle, `--kind code` yalnızca kaynakla sınırlar.

## Bir ajana kullandırın

DeerX bir MCP sunucusu taşıyor. Onu bilgi tabanına yöneltirseniz her MCP
istemcisi — Claude Code, Claude Desktop, kendi ajanınız — burayı bir araç
olarak arayabilir:

```json
{
  "mcpServers": {
    "deerx-kb": {
      "command": "uv",
      "args": ["run", "--project", "/yol/DeerX-App-Development-Platform",
               "deerx", "mcp", "--workspace",
               "/yol/DeerX-App-Development-Platform/.deerx-kb"]
    }
  }
}
```

Burada işe yarayan araçlar `deerx_search` (hibrit arama, kaynaklarıyla pasaj
döner) ve `deerx_documents` (ne indekslenmiş). Tam liste için
[MCP sunucusu](mcp.md).

## Güncel tutmak

İndeks içerik adresli: yeniden kurmak yalnızca içeriği değişmiş dosyaları
yeniden okur, yani birkaç dosya düzenledikten sonra tekrar koşmak hızlıdır.

```bash
uv run python scripts/knowledge/build.py
```

Koddan sapmış bir bilgi tabanı, hiç olmamasından kötüdür: bir zamanlar doğru
olan bir şeyi kendinden emin biçimde söyler. Cevaplara yansımasını
isteyeceğiniz her değişiklikten sonra yeniden kurun.

## Bu ne değildir

Bu bir **başvuru** tabanı, bir hafıza değil: deponun söylediğini tutar, bir
konuşmada karar verdiğinizi değil. Proje durumu — gereksinimler, boşluklar,
kararlar, görevler — her çalışma alanının kendi `.deerx/deerx.db` dosyasında
yaşar ve [MCP sunucusu](mcp.md) ya da web arayüzü üzerinden okunur.
