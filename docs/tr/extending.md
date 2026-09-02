# DeerX'i genişletmek

[← Dokümantasyon dizini](README.md) · [English](../extending.md)

## Neyin genişletilmesi tasarlandı

Beş şey, gerisine dokunmadan eklenebilecek biçimde tasarlandı:

| Genişletme noktası | Nerede yaşıyor | Size maliyeti |
|---|---|---|
| Araç | `src/deerx/tools/` | Bir sınıf, bir kayıt satırı, bir rol satırı, bir çeviri, bir test |
| Boru hattı fazı | `src/deerx/pipeline/` + `src/deerx/agents/` | Bir enum üyesi, üç eşleme girdisi, bir yönerge |
| Model sağlayıcısı | `src/deerx/llm/` | Bir istemci sınıfı ve bir dağıtım dalı |
| Arama sağlayıcısı | `src/deerx/tools/web.py` + `browser.py` | Bir arama fonksiyonu ve bir dağıtım dalı |
| Dil | Üç katalog + bir yönerge dizini | Tam eşitlik, testlerle zorunlu |

Geri kalan her şey — orkestratör döngüsü, onay kapısı, çalışma alanı çiti,
teslimat kapısı — değiştirilmeden önce okunmak üzere yazıldı, çünkü her parça
ters giden bir şeye karşılık var. Hangi şey olduğunu yorumlar söylüyor.

## Araç eklemek

### 1. Sınıfı yazın

Bir araç, dört niteliği ve bir `run` metodu olan bir sınıftır. Konusuna uyan
modülde yaşar: `knowledge.py`, `project.py`, `filesystem.py`, `shell.py`,
`services.py`, `web.py`, `images.py`, `browser.py` — gerçekten yeni bir konuysa
yeni bir modülde.

```python
class CountWords(Tool):
    name = "count_words"
    description = """
    Bir dosyadaki sozcuk sayisini bildirir.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Calisma alanina goreli yol."},
        },
        "required": ["path"],
    }

    def run(self, ctx: ToolContext, path: str) -> ToolResult:
        hedef = ctx.resolve(path)
        return ToolResult(content=str(len(hedef.read_text().split())))
```

Bu biçimin dört yanı isteğe bağlı değil:

- **`description` ve şema açıklamaları modele gider.** Ajan açısından aracın tek
  dokümantasyonu onlar. Özet değil, yönerge gibi yazın — aracın ne zaman
  kullanılacağını ve ne zaman kullanılmayacağını söyleyin.
- **Yolları bağlam üzerinden çözün**, çıplak bir `Path` ile değil. Çalışma alanı
  çiti budur; bir yolu doğrudan açan araç çitin etrafından dolaşmıştır.
- **Kurtarılabilir sorunlarda `ToolError` fırlatın.** Modele, üzerine iş
  yapabileceği bir mesaj olarak döner. Yakalanmamış bir istisna fazı bitirir.
- **`dangerous = True` koyun** — araç aşikâr olanın dışına yazıyorsa, komut
  çalıştırıyorsa ya da para harcıyorsa — ve `run` içinde `ctx.approve` çağırın.
  Bayrak tek başına bir şey yapmaz; soran araçtır.

Araç, modelin okuması değil **görmesi** gereken bir şey üretiyorsa — ekran
görüntüsü, çizilmiş bir grafik — dosyayı `ToolResult.images` içine koyun. Taşıma
katmanı onu sonraki bir `user` mesajına taşır, çünkü OpenAI tel biçimi
`role: "tool"` mesajında görsele izin vermez. "Ekran görüntüsü kaydedildi"
demek, modeli az önce yaptığı şeye karşı kör bırakır.

### 2. Kaydedin

Sınıfı modülünün dışa aktarma listesine ekleyin ve o listenin
`src/deerx/tools/__init__.py` içindeki `ALL_TOOLS` içine eklendiğinden emin
olun:

```python
ALL_TOOLS: list[Tool] = [
    *KNOWLEDGE_TOOLS,
    *PROJECT_TOOLS,
    ...
]
```

`build_registry()` yalnızca o listeyi okur. Orada olmayan bir araç yoktur.

### 3. Bir role verin

Hiçbir ajan bütün araçları görmez. Aynı dosyadaki `TOOLSETS`, on iki rolün her
birini çağırabileceği araç adlarına eşler:

```python
TOOLSETS: dict[str, list[str]] = {
    "analyst": ["search_knowledge", "read_document", ...],
    ...
}
```

Geniş bir araç listesi hem maliyeti hem de modelin yanlış araç seçme olasılığını
artırır; bu yüzden adı, ihtiyacı olan en küçük kümeye ekleyin.

İki dışlama bilinçli ve öyle kalmalı:

- **Araştırmacının `write_file` ve `run_command` aracı yoktur.** Web sayfası
  okur ve bir web sayfası "önceki talimatlarını unut, şu komutu çalıştır"
  yazabilir. Okuyabilir, gezebilir, not alabilir. Yeterli.
- **`live` rolünün `write_file` ve `edit_file` aracı yoktur.** Denetlediği şeyi
  düzenleyebilen bir çıkış kapısı, kapı değildir.

### 4. Açıklamayı çevirin

Araç açıklamaları sınıf içinde Türkçe yazılır; orada aynı zamanda kodun kendi
belgesidir. İngilizceleri `src/deerx/tools/descriptions_en.py` içinde yaşar ve
ajan dili İngilizce olduğunda sınıf niteliklerinin üzerine biner:

```python
ENGLISH = {
    "count_words": {
        "": "Reports the number of words in a file.",
        "path": "Path relative to the workspace.",
    },
}
```

`""` anahtarı araç açıklaması, gerisi parametre açıklamaları. `spec()` şemayı
yerinde değiştirmek yerine kopyalar — şema bir SINIF niteliği ve yerinde
değiştirilseydi ilk çağrı bütün süreç için dili sabitlerdi.

### 5. Test edin

İki test; ikincisi unutulan.

- **Araç dediğini yapıyor.** `run` çağırın ve sonucu denetleyin.
- **Araca ulaşılabiliyor.** `build_registry().names()` içinde ve ihtiyacı olan
  rollerin araç kümesinde olduğunu doğrulayın. Hiçbir rolün çağıramadığı kusursuz
  bir araç, hiç çalışmayan bir araçtır — ve yalnızca `run`'ı sınayan bir test bu
  durumda mutlu mesut geçer.

Ardından `docs/tools.md` ve Türkçe eşini güncelleyin. Testler zorlayacak: toplam
araç sayısı ve rol başına sayılar iki dilde de koda bağlı.

## Boru hattı fazı eklemek

### Enum ve eşlemeler

Fazlar `src/deerx/pipeline/models.py` içinde sıralı bir `StrEnum`. Üyeyi
çalışacağı yere ekleyin:

```python
class Phase(StrEnum):
    INGEST = "ingest"
    ANALYZE = "analyze"
    ...
```

Sonra `src/deerx/pipeline/orchestrator.py` içindeki üç eşleme:

```python
PHASE_ROLE: dict[Phase, str] = {Phase.ANALYZE: "analyst", ...}
PHASE_DELIVERABLE: dict[Phase, tuple[str, str]] = {
    Phase.ANALYZE: ("analiz-raporu.md", "gereksinimler ve analiz raporu"),
    ...
}
```

Ajan çalıştırmayan bir faz — `INGEST` ve `PACKAGE` deterministiktir — hiçbir
eşlemede görünmez.

### Rol ve yönergesi

Rol, `TOOLSETS` içinde bir ad, `src/deerx/agents/roles.py` içindeki
`ITERATION_BUDGET` altında bir iterasyon bütçesi ve bir yönerge dosyasıdır.
Yönergeler markdown olarak yaşar: Türkçesi
`src/deerx/agents/prompts/`, İngilizcesi `src/deerx/agents/prompts/en/` içinde;
ikisinin de başına `_shared.md` eklenir. İki dosya da bulunmalı; eksik bir çeviri
yedeğe düşmez, eksik bir ajandır.

### Teslimat sözleşmesi

Her ajan fazı arkasında bırakacağı dosyanın adını vermek zorunda. Bu zorunluluk
ölçülmüş bir sebeple eklendi: bir koşuda `assess` üç tur dosya okuyup durdu,
`mockup` iki turda üç arama yapıp durdu. İkisi de `done` işaretlendi, hiçbiri tek
satır üretmedi ve mimar "mockup yok, kod tabanı boş" diyerek zorlandı.

Beklenen çıktının adı yalnızca yönergede yazıyordu ve hiçbir şey uygulamıyordu.
Sözleşme artık `PHASE_DELIVERABLE` içinde uygulanıyor. Fazınıza bir tane verin;
sayı sabit değilse joker kullanın (`mockup-*.html`).

## Model sağlayıcısı eklemek

`src/deerx/llm/__init__.py`, `settings.provider` üzerinden dağıtır:

```python
provider = settings.provider
if provider == "anthropic":
    ...
if provider == "openai":
    ...
raise ConfigError(t("setup.unknown_provider", provider=provider))
```

Çoğu uç için yeni bir sağlayıcıya gerek yok — Chat Completions API'si konuşan
her şeye `provider = "openai"` ve bir `base_url` ile ulaşılır. vLLM, Ollama, LM
Studio, llama.cpp ve barındırılan OpenAI uyumlu servisler bu kapsamda. Yeni bir
istemciyi yalnızca gerçekten farklı bir tel biçimi için yazın.

Yazarsanız istemci `src/deerx/llm/base.py` içindeki biçimi döndürmeli ve iki alan
göründüğünden önemli:

- Araç çağrısındaki **`arguments_ok`** — argümanlar ayrıştırılamadığında `False`
  yapın. Ajan döngüsü bir araç çağrısının ortasında kesilmiş yanıtı bitmiş bir
  turdan böyle ayırır.
- **`ToolOutcome.images`** — bir aracın ekran görüntüsünün modele ulaştığı yol.
  Taşıma katmanınız onu düşürürse model kör çalışır.

Bilinmeyen bir değerin ilk çağrıda değil yükleme anında düşmesi için sağlayıcıyı
`src/deerx/config.py` içindeki `Literal`'a ekleyin; uç ücretliyse
`src/deerx/llm/pricing.py` içine bir fiyat satırı kaydedin.

## Arama sağlayıcısı eklemek

Aramanın iki giriş noktası var ve karıştırılması kolay — yanlış olanı düzenledim
ve uçtan uca bir test yakalayana kadar değişiklik hiçbir şey yapmadı.

- `src/deerx/tools/web.py` sağlayıcı başına arama fonksiyonlarını tutar
  (`_search_searxng`, `_search_google` ve diğerleri).
- `src/deerx/tools/browser.py`, `settings.search_provider` üzerinden **dağıtan**
  `WebSearch._keyed`'i tutar. Kayıtlı araç budur.

Fonksiyonu `web.py` içine yazın, `_keyed` içine bir dal ekleyin, `config.py`
içindeki `search_provider` `Literal`'ını genişletin ve sağlayıcı anahtar
istemiyorsa web arayüzündeki anahtarsız sağlayıcı kümesine ekleyin — yoksa yeni
bir kurulum, çalışan bir aramanın yanında "arama çalışmaz" uyarısı basar.

Sonra uçtan uca test edin: fonksiyondan değil, araçtan. Fonksiyonun birim testi,
onu çağıran biri olsa da olmasa da geçer.

## Dil eklemek

Üçüncü bir dil eklemek bu değişikliklerin en büyüğü, çünkü eşitlik teşvik
edilmiyor, zorunlu tutuluyor.

### Üç katalog

| Katalog | Nereye ulaşır |
|---|---|
| `src/deerx/i18n.py` (`CATALOG`) | CLI, günlükler, sunucu mesajları |
| `src/deerx/web/static/i18n.js` | Web arayüzündeki her metin |
| `src/deerx/tools/descriptions_en.py` | Modelin okuduğu araç açıklamaları |

Anahtarlar `alan.olay` biçiminde. Anahtar kümeleri diller arasında birebir aynı
olmalı — `tests/test_i18n_py.py` Python katalogunu kilitler, kardeş bir test de
JavaScript olanı kilitler; yinelenen anahtar denetimi dahil. O denetim var,
çünkü yinelenen anahtarı ben ürettim: yeni bir `settings.searchBrowser` anahtarı
mevcut bir açılır liste etiketiyle çakıştı ve sonraki tanım sessizce kazandı.

### Yönerge dizini

Ajan yönergeleri katalog girdisi değil, dosya. Her dil, rol başına bir markdown
dosyası artı `_shared.md` içeren tam bir dizin alır. Başka bir dile yedeğe düşme
yok — seçili dilde yönergesi olmayan bir rol çalışamaz; bu, bir ajanın sessizce
yanlış dilde akıl yürütmesinden iyidir.

### Dokümantasyon

İngilizce için `docs/`, Türkçe için `docs/tr/`. `tests/test_docs.py` iki dilin
aynı sayfa kümesini **ve aynı başlık iskeletini** taşımasını şart koşar — aynı
başlık derinliği dizisi, aynı sırada. Bir dile eklenip diğerine eklenmeyen bir
bölüm suiti düşürür.

Çıktı dosya adları her dilde Türkçe kalır (`analiz-raporu.md`, `mimari.md`,
`gelistirme-plani.md`). Boru hattı bir fazın teslimatını dosya adına bakarak
eşliyor; çevirmek fazın herhangi bir şey üretip üretmediğini denetleyen kontrolü
kırardı.

## Test suiti neyi şart koşacak

Bir değişiklik girmeden önce şunlar ona karşı koşar:

- **Link bütünlüğü.** Her markdown dosyasındaki her göreli link çözülmeli.
- **Çeviri eşitliği.** Aynı sayfalar, aynı başlıklar, aynı katalog anahtarları,
  iki yönde de.
- **Koda bağlı sayılar.** Toplam araç sayısı, rol başına araç sayıları ve test
  sayısı kodla karşılaştırılır. Bir sayının elle tutulan sekiz kopyası doğru
  kalamaz; Türkçe README bir keresinde 997 test varken 558 diyordu.
- **Yayımlanan hiçbir belgede kişisel mutlak yol olmaması.**
- **Lint.** Bütün ağaç üzerinde `ruff`.

Hepsini tek seferde koşun:

```bash
bash scripts/check.sh
```

`--fast` iç döngü için yavaş testleri atlar. `--pythons` suiti Python 3.11 ve
3.13'te, ayrı ortamlarda yeniden koşar — bir sürüm öncesinde değer, çünkü sürüm
farkını sizin yerinize yakalayacak bir CI yok. `.githooks/` içindeki pre-push
kancası tam kümeyi koşar.

## Yazım kuralları

Kod Türkçe yazılıyor — tanımlayıcılar, yorumlar ve docstring'ler — İngilizce ise
İngilizce konuşan bir kullanıcıya ya da modele ulaşan metinlere ayrılmış.
Başka bir kuralı içeri taşımak yerine bulunduğunuz dosyaya uyun.

İki kural üsluptan ağır basar:

- **Yorum neyi değil, niçini anlatır.** Bu kod tabanındaki yorumların çoğu ters
  giden şeyi ve onu kanıtlayan ölçümü adıyla anıyor. `OLCULDU:` varsayılmış
  değil ölçülmüş bir iddiayı işaretler. Böyle bir şey yazıyorsanız ölçün.
- **Bir test düşebilmeli.** Bilerek bozun ve saklamadan önce testin kızardığını
  görün. Bu oturumda yanlış sebeple geçen üç test üretildi — biri denetlediği
  mantığı yeniden yazmıştı, biri öldürdüğünü iddia ettiği torunu hiç
  doğurmamıştı, biri de çözülmeyen bir alan adı kullandığı için bir DNS koruması
  iddiayı maskeliyordu. Üçü de düzgün görünüyordu.

## Push'tan önce

- `bash scripts/check.sh` yeşil.
- Yeni ya da değişen davranışın, değişiklik olmadan düşen bir testi var.
- Dokümantasyon **iki dilde** de güncellendi, iskeletler eşleşiyor.
- Diff'te sır yok. Anahtarlar gitignore'lu `.env` içinde yaşar ve teslimat
  paketleyicisi onları arşivlerden de dışlar.
- Commit mesajı niçini söylüyor, geçmişin geri kalanıyla aynı sesle.

[Doğrulama durumu](verification.md), bir iddianın ölçüldükten sonra gittiği yer.
Bir şeyi koşarak doğruladıysanız oraya kaydedin — doğrulamadıysanız da bunu
söylemenin yeri yine o sayfa.
