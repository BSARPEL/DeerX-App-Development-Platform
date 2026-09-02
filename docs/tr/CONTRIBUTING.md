# Katkı

Baktığınız için teşekkürler. Bu belge kod tabanının nasıl çalıştığını anlatır ki
gönderdiğiniz değişiklik bir tur stil notu almadan yerine otursun.

[← Dokümantasyon](README.md) · [English](../../CONTRIBUTING.md)

## Kurulum

```bash
uv sync --extra embed --extra dev
uv run deerx doctor
```

İsteğe bağlı ekler: Playwright destekli araçlar için `--extra browser`.

## İki kontrol

```bash
uv run pytest
```

```bash
uv run ruff check src tests
```

İkisi de geçmeli. Ayrı bir biçimlendirme adımı yok — çubuk `ruff check` ve kod
tabanı bilinçli olarak `ruff format` uyumlu değil.

Üçüncü bir araç var ama **kapı değil**: `mypy` `dev` ekiyle kuruluyor ve
`pyproject.toml` içinde yapılandırılmış, ancak `check.sh` onu koşturmuyor ve
push'unuzu engellemiyor.

```bash
uv run mypy src/deerx
```

28 bulgudan oluşan bir temel çizgi var. Geçmeyen bir denetim, herkesin yok
saymayı öğrendiği bir denetimdir; o yüzden sıfıra inene kadar kapının dışında
duruyor. Yine de koşturmaya değer: ilk koşu gerçek bir kusur buldu — kurulum
yoklaması var olmayan bir metodu çağırıyordu, gömme modeli hiç indirilmiyordu
ve yutulan `AttributeError` başarısız bir indirme gibi görünüyordu.

> İngilizce [CONTRIBUTING.md](../../CONTRIBUTING.md) bu sayfadan daha
> ayrıntılı: `scripts/check.sh`, pre-push kancası ve sürümler arası koşu
> yalnızca orada anlatılıyor. Bu sayfa henüz onunla eşitlenmedi.

## Ev kuralları

**Yorumlar ve tanımlayıcılar Türkçe ve ASCII'ye indirgenmiş.** `calisma_alani`,
`working_directory` ya da `çalışma_alanı` değil. Bu baştan sona tutarlı; ikinci
bir kural başlatmak yerine uyum sağlayın. Kullanıcıya giden *metinler* ayrı bir
mesele — aşağıya bakın.

**Kullanıcıya ya da modele giden her metin kataloğdan geçer.**

```python
raise ToolError("Dosya bulunamadi")        # hayır
raise ToolError(t("fs.not_found"))         # evet — metin i18n.py'de
```

`tests/test_no_hardcoded_turkish.py` kaynağı bir AST yürüyüşüyle tarar ve
kullanıcıya ya da modele ulaşan bir metin sabitse düşer. İki dil de doldurulmuş
olmalı ve `{yer_tutucu}`ları eşleşmeli — o da test ediliyor.

Araç *açıklamaları* istisnadır: Türkçe metin aracın sınıfında kalır, çünkü
davranışın belgelendiği yer orasıdır; İngilizcesi
`src/deerx/tools/descriptions_en.py` içindedir. Bir test her aracın ve
açıklamalı her parametrenin ikisinde de olduğunu kontrol eder.

**Yorumlar neyi değil niçini anlatır.** Kod tabanı buna çok yaslanıyor. Üstündeki
satırı tekrar eden bir yorum gürültüdür; o satırı üreten ölçümü ya da hatayı
kaydeden yorum ise kodun okunabilir olma sebebidir. Ton için `tools/shell.py` ya
da `pipeline/orchestrator.py` dosyalarına bakın.

**Önce düşen testi yazın.** Tören olsun diye değil — testin gerçekten ısırdığını
kontrol etmek için. Düzeltilmemiş koda karşı geçen bir test hiçbir şeyi test
etmez ve bu burada daha önce yaşandı.

## Testler

`tests/` paketi yansıtır. Birkaç dosyanın özel işleri var:

| Dosya | Neyi koruyor |
|---|---|
| `test_regressions.py` | Bir zamanlar sessizce yayımlanmış her hata için bir test |
| `test_no_hardcoded_turkish.py` | Kullanıcıya/modele giden her metnin katalogda olması |
| `test_i18n_py.py` · `test_i18n.py` | Katalog biçimi, yer tutucu eşliği, dilin gerçekten değişmesi |
| `test_scripts.py` | Yönetim betikleri, yoklanan yolların public olması dahil |
| `test_web.py` | HTTP API, SSE döngüsü, palet ve tasarım ölçeği |

Testler sahte bir LLM istemcisi kullanır (`tests/conftest.py`); süitte hiçbir
gerçek model çağrısı yoktur ve ağ gerekmez.

## Bir şey eklemek

Bu bölümün her kısıtın gerekçesini de veren uzun hâli:
[DeerX'i genişletmek](extending.md). Eksik değil bozuk bir şey varsa,
[Sorun giderme](troubleshooting.md) burada gerçekten yaşanmış belirtileri
listeliyor.

**Yeni bir araç** — doğru `tools/` modülünde `Tool`'dan türetin,
`build_registry()`'nin görmesi için `ALL_TOOLS`'a ekleyin, Türkçe açıklamayı
sınıfa, İngilizcesini `descriptions_en.py` içine koyun ve adını,
`tools/__init__.py` içindeki `TOOLSETS` altında ihtiyacı olan her role ekleyin
— `agents/roles.py` o eşlemeyi okur, tutmaz. Her hatayı `t()` üzerinden
geçirin.

**Yeni bir faz** — `pipeline/models.py` içindeki `Phase`'e ve `Phase.ordered()`
listesine ekleyin, çıktı üretiyorsa orkestratördeki `PHASE_DELIVERABLE`'a
ekleyin, ve `phase.<id>` / `agent.<id>` / `produces.<id>` anahtarlarını **hem**
`i18n.py` **hem** `web/static/i18n.js` içine ekleyin. Bir test iki kataloğun
aynı fazları kapsadığını doğrular.

**Yeni bir ayar** — alanı `config.py` içindeki `Settings`'e, şablonu
`templates/deerx.default.toml` içine ekleyin, ve arayüzden düzenlenebilir
olacaksa `web/app.py` içindeki `SETTING_FIELDS`'e. `deerx.toml` içindeki
tanınmayan anahtarlar yazım önerisiyle bir uyarı üretir, yani bir yazım hatası
kaybolmaz.

**Yeni bir ajan prompt'u** — `agents/prompts/<rol>.md` ve
`agents/prompts/en/<rol>.md`. Prompt'ların içindeki çıktı dosya adları Türkçe
kalır: boru hattı teslimatları ada bakarak eşler, dolayısıyla çevirmek fazın bir
şey ürettiğini denetleyen kontrolü kırardı.

## Commit'ler

Mesajlar Türkçe ve mevcut günlüğü izler: neyin değiştiğini ve neden önemli
olduğunu söyleyen bir başlık, sonra değişikliğin ele aldığı hatayı anlatan bir
gövde. `git log` burada dokümantasyondur — lütfen öyle kalmasını sağlayın.

`main`'den dallanın. Bir pull request'te bir konu.

## Güvenlik açığı bildirimi

Herkese açık bir issue açmayın. [SECURITY.md](../../SECURITY.md) dosyasına
bakın.
