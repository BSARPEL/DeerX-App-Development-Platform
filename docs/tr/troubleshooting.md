# Sorun giderme

[← Dokümantasyon dizini](README.md) · [English](../troubleshooting.md)

## Bu sayfa nasıl okunur

Aşağıdaki her madde **gerçekten yaşanmış bir belirti**, ardından ölçülen sebep
ve işe yarayan çözüm. Burada varsayım yok; bir sebep yeniden üretilmek yerine
çıkarımla bulunduysa madde bunu söylüyor.

Maddeler hatanın yaşadığı yere göre değil, belirtinin göründüğü yere göre
gruplandı — çünkü bir şey bozulduğunda ikincisini değil birincisini bilirsiniz.

Aşağıdakilerin çoğunu bu sayfaya gelmeden çözen iki alışkanlık:

- **Ekranı değil günlüğü okuyun.** `deerx run` bir özet basar; sebebi
  `<calisma-alani>/.deerx/events.jsonl` içindeki olay günlüğü basar.
- **Koşudan önce bağlantıyı test edin.** Ayarlar ekranında gerçek çağrı yapan üç
  düğme var. Model adının yanlış olduğunu öğrenmek için boru hattının kırkıncı
  dakikası kötü bir an.

## Model

### Her çağrı 401 Unauthorized ile düşüyor

**Belirti.** Her faz anında
`Error code: 401 - {'error': 'Unauthorized'}` ile ölüyor, buna karşılık Ayarlar
ekranı modeli "hazır" gösteriyor.

**Sebep, ölçüldü.** `Settings.llm_ready`, yerel bir OpenAI uyumlu uç için taban
adresini yeterli sayıyor — "çoğu yerel sunucu anahtar istemez" varsayımıyla. Bu
varsayım `--api-key` ile başlatılmış bir vLLM sunucusu için yanlış. Hazırlık
göstergesi ucu değil, varsayımı bildiriyordu.

**Çözüm.** Anahtarı tanımlayın. Çalışma alanındaki `.env` içinde:

```bash
OPENAI_API_KEY=...
```

ya da Ayarlar ekranından girin; orada yalnızca yazılabilir — tanımlanabilir ve
değiştirilebilir, geri okunamaz.

DeerX artık çareyi çıplak 401'in kendisine ekliyor: "anahtar tanımlı değil" ile
"uç tanımladığınız anahtarı reddetti" durumlarını ayırıyor, çünkü bunlar farklı
sorunlar ve ham 401 sizi yanlış yerde arattırıyor.

### Koşu erken bitiyor ve faz raporu yarım kalıyor

**Belirti.** Faz `done` bildiriyor ama teslimatı cümle ortasında kesiliyor ve
sonraki fazlar eksik girdiyle çalışıyor.

**Sebep, ölçüldü.** Yanıt üretim tavanında kesildi. Bu bir araç çağrısının
ortasında olursa yarım kalan çağrı düşüyor ve `tool_calls` boş geliyor — yani
kesilmiş bir yanıt, işini bitirmiş bir yanıttan ayırt edilemiyordu ve döngü
sessizce sona eriyordu. On üç fazlık bir koşuda `assess` fazı tam 16000 token'da
durdu.

**Çözüm.** `max_tokens` değerini yükseltin ve asıl sınırın bağlam tavanı
olmadığını doğrulayın:

```toml
[deerx]
max_tokens = 32000
```

Döngü artık kesilmenin iki biçimini de yakalıyor — `stop_reason = "max_tokens"`
ile gelen boş `tool_calls` ve argümanları ayrıştırılamayan bir araç çağrısı — ve
modelin döngüden düşmesine izin vermek yerine ona kesildiğini söylüyor.

### Model konuşuyor ama hiç araç çağırmıyor

**Belirti.** Faz günlüğe düzyazı üretiyor, hiç çıktı üretmiyor ve `done`
işaretleniyor.

**Sebep.** Bazı uçlar araç kullanımı için eğitilmemiş bir model sunar ya da araç
bloğunu düşüren bir şablonla sunar. Faz sözleşmesi tam da bunun için var:
"model konuşmayı bıraktı", "model işini bitirdi" demek değil.

**Çözüm.** Her ajan fazı adı belli bir teslimat bırakmak zorunda
(`analiz-raporu.md`, `mimari.md` ve diğerleri — bkz.
[Boru hattı](pipeline.md)). Dosya yoksa faz kabul edilmez. Her fazda yoksa model
hiç araç çağırmıyor demektir: aynı uçta başka bir model deneyin ve **Bağlantıyı
test et** ile ucun araç kullanım biçimine cevap verdiğini doğrulayın.

## Yapılandırma

### `deerx.toml` içindeki bir ayar hiçbir şey yapmıyor

**Belirti.** `search_provider = "searxng"` ve `approval_mode = "auto"` yazdınız,
hiçbir şey değişmedi ve hiçbir uyarı çıkmadı.

**Sebep, ölçüldü.** Anahtarlar dosyanın başında, `[deerx]` tablosunun
dışındaydı. O tablonun dışındaki her şey yok sayılır — üstelik tanınmayan anahtar
denetimi boş kök sözlükle çalıştığı için yazım hatası bile uyarı üretmiyordu.

**Çözüm.** Dosyaya başlığını verin:

```toml
[deerx]
search_provider = "searxng"
approval_mode = "auto"
```

DeerX artık bunu yutmak yerine yüksek sesle söylüyor:

> deerx.toml icindeki ayarlar YOK SAYILDI: search_provider. Ayarlar [deerx]
> tablosunun altinda olmali. Dosyanin basina [deerx] satirini ekleyin.

### Ayarlar ekranı boş açılıyor

**Belirti.** Yeni bir kurulumda Ayarlar sekmesindeki her alan boş, sanki hiçbir
şeyin varsayılanı yokmuş gibi.

**Sebep.** Sekme, değerleri taşıyan genel bakış isteği dönmeden çiziliyordu.
Hata yoktu — yalnızca boş bir form vardı ve bu "hiçbir şey yapılandırılmamış"
diye okunuyor; oysa her şey varsayılanlarla yapılandırılmış durumda.

**Çözüm.** Arayüzde düzeltildi: sekme artık genel bakışı bekliyor ve
varsayılanları çiziyor. Hâlâ boş görüyorsanız genel bakış isteğinin kendisi
düşüyordur — tarayıcı konsoluna ve sunucu günlüğüne bakın.

### Ekran ile dosya farklı şeyler söylüyor

**Belirti.** `deerx.toml` bir şey diyor, Ayarlar ekranı başka bir şey gösteriyor.

**Sebep.** Öncelik sırası. Ortam değişkenleri `.env` dosyasını, o da
`deerx.toml`'u, o da gömülü varsayılanları geçer. Sunucuyu başlatan kabukta
export edilmiş bir değer, düzenlediğiniz dosyayı yener.

**Çözüm.** Bir ayarın nerede yaşayacağına karar verin ve orada tutun. Sırlar
`.env` içinde (gitignore'lu), geri kalan her şey `deerx.toml` içinde. Tam
öncelik tablosu için [Yapılandırma](configuration.md).

## Web araştırma

### Arama hiçbir şey döndürmüyor

**Belirti.** Araştırma fazı kaynaksız notlar üretiyor ve Ayarlar ekranı aramanın
çalışmayacağına dair kırmızı bir uyarı gösteriyor.

**Sebep, ölçüldü.** Altı sağlayıcıdan üçü — `browser`, `duckduckgo` ve `searxng`
— anahtar istemez, ama durum satırı hepsinin istediğini varsayıyordu; böylece
yeni bir kurulum, çalışan bir aramanın yanında uyarı basıyordu.

**Çözüm.** Uyarı artık sağlayıcının gerçek lisans durumunu bildiriyor. Arama
gerçekten hiçbir şey döndürmüyorsa sağlayıcıyı açıkça yazın ve test edin:

```toml
[deerx]
search_provider = "searxng"
searxng_url = "http://127.0.0.1:8890"
```

Ardından **Aramayı test et** düğmesine basın; o düğme yapılandırmayı denetlemez,
gerçek bir sorgu yapar.

### Google 400 dönüyor

**Belirti.** `search_provider = "google"` hiçbir şey anlatmayan bir mesajla 400
veriyor.

**Sebep.** Google'ın Programmable Search JSON API'si **iki** değer ister — API
anahtarı ve arama motoru kimliği — ve biri eksikse işe yaramaz bir 400 döner.

**Çözüm.** İkisini de tanımlayın:

```toml
[deerx]
search_provider = "google"
google_cse_id = "..."
```

```bash
# .env
SEARCH_API_KEY=...
```

DeerX ham 400'ü geçirmek yerine eksik olanı adıyla söyler. Ücretsiz katman günde
100 sorgu; bir araştırma fazı birkaç tane kullanır.

### Tarayıcı sağlayıcısı bot korumasına takılıyor

**Belirti.** `search_provider = "browser"`,
*"Our systems have detected unusual traffic from your computer network."* yazan
bir sayfa döndürüyor.

**Sebep, ölçüldü.** Google'ın arama sayfası otomatik tarayıcıyı doğrudan
reddediyor — görünmez olanı değil, gerçek Chrome'u bile.

**Çözüm.** Bot koruması aşmayı gerektirmeyen bir sağlayıcı kullanın: `searxng`
(kendi örneğiniz, anahtar yok, kota yok — üstelik Google'ı sunucu tarafında
sorgulayabilir), `duckduckgo` ya da yukarıdaki gibi lisanslı API'siyle Google.
Bot korumasını aşmak bu projenin yaptığı bir şey değil.

### Araştırma URL uyduruyor ve tur bütçesini yakıyor

**Belirti.** Araştırma fazı olay günlüğünü `fetch_url` hatalarıyla dolduruyor —
HTTP 404'ler ve var olmayan alan adlarında *"alan adı çözülemedi"* — ve sonunda
tur bütçesinin sonuna yaklaştığını bildiriyor. Gerçek bir koşuda ölçüldü: dokuz
404, dört çözülemeyen alan adı, on dört tur.

**Sebep, ölçüldü.** Bu hatalar *sonuç*, sorun değil. Yukarıya doğru bir
`web_search` hatası arayın. `bing: net::ERR_ABORTED` diyorsa arama genel bir
motoru tarayıcıyla kazımaya düşmüş ve engellenmiştir — yani ajan URL
**bulamıyor**, tahmin etmeye başlıyor.

Yaygın sebep, çalışan bir SearXNG'in kurulu ama seçili olmaması. `deerx setup`
konteyneri başlatıyor ve `search_provider`'ı sizin için çeviriyor, ama ancak
orada olan bir ayarı çevirebilir: elle sadeleştirilmiş, `search_provider` satırı
hiç olmayan bir `deerx.toml` eskiden sessizce `browser` kalıyordu. Gerçekte ne
geçerli olduğuna bakın:

```bash
uv run deerx doctor
```

**Çözüm.** Ayarı `[deerx]` tablosunun içine koyun — dosyanın sonuna değil, çünkü
TOML onu son alt tabloya bağlar ve hiçbir şey yapmaz:

```toml
[deerx]
search_provider = "searxng"
searxng_url = "http://127.0.0.1:8890"
```

Sonra örneğin JSON döndürdüğünü doğrulayın; varsayılan olarak kapalıdır:

```bash
curl "http://127.0.0.1:8890/search?q=test&format=json"
```

`deerx setup`'ı yeniden koşturmak da düzeltir — satır hiç yoksa bile.

## Ajanın yazdığını çalıştırmak ve test etmek

### `run_command` bir komutu reddediyor

**Belirti.** Ajan bir komut deniyor ve araç çıktı yerine ret dönüyor.

**Sebep.** Kabuk izin listesi. Konakta ajan yalnızca politikanın izin verdiğini
çalıştırabilir — bir modelin hatasını sizin sorununuz olmaktan çıkaran çit bu.

**Çözüm.** Ya politikayı `deerx.toml` içinde bilinçli olarak genişletin ya da
çalıştırmayı çitin gereksiz olduğu bir konteynere taşıyın:

```toml
[deerx]
execution = "docker"
```

Konteyner içinde izin listesi uygulanmaz, çünkü korunacak bir konak yoktur ve
konteyner koşu bitince silinir. Neyin yalıtıldığı ve neyin yalıtılmadığı için
[Güvenlik](security.md) — çalışma alanı bağlanır, yani makine korunur ama proje
korunmaz.

### Servis başlıyor ama port hiç cevap vermiyor

**Belirti.** `start_service` servisi başladı diye bildiriyor ve portta hiçbir şey
cevap vermiyor.

**Sebep, ölçüldü.** İkisi de bana ait iki ayrı hata. Portlar konteyner
kurulurken yayınlanır ve Docker sonradan port ekleyemez; yayınlanan aralığın
dışından port seçen bir servise erişilemez. Ayrıca hazırlık denetimi konaktan
yokluyordu, yani içeride hiçbir şey dinlemezken "hazır" diyebiliyordu.

**Çözüm.** Servis, yayınlanan aralıktan bir port seçmeli ve konteyner içinde
`0.0.0.0`'a bağlanmalı:

```toml
[deerx]
sandbox_port_base = 8100
sandbox_port_count = 10
```

`--network host` Windows konağına **ulaşmaz**; yalnızca yayınlanan portlar ulaşır.

### Konteynerde `git`, `gcc` ya da `node` yok

**Belirti.** Ajanın derleme adımı eksik bir araç yüzünden düşüyor.

**Sebep, ölçüldü.** `python:3.13-slim` içinde `git`, `curl`, `gcc` ve `make`
yok. Varsayılan imaj bilinçli olarak slim sürümü **değil**.

**Çözüm.** Varsayılanı koruyun ya da gerekeni konteyner kurulurken bir kez
yükleyin:

```toml
[deerx]
sandbox_image = "python:3.13"
sandbox_setup = "apt-get update && apt-get install -y nodejs npm"
```

### `docker run` "port is already allocated" diyor

**Belirti.** `execution = "docker"` ile koşu hemen düşüyor:
`Bind for 127.0.0.1:8100 failed: port is already allocated`.

**Ölçülen sebep.** Konteyner adı çalışma alanının yolundan türetilir, yani iki
çalışma alanı iki ayrı konteyner alır — ama *yayınlanan port aralığı* ikisinde
de aynı `8100-8109`. Docker yayınlanan portları konteyner yaratılırken ayırır,
dolayısıyla birincisi ayaktayken ikincisi başlayamaz. Önceki bir koşunun
konteyneri hiç silinmemişse de aynı şey olur.

**Çözüm.** İkinci çalışma alanına kendi aralığını verin — **Ayarlar → Yalıtım**
ekranından ya da o alanın `deerx.toml` dosyasından:

```toml
[deerx]
sandbox_port_base = 8200
```

Ya da portları tutan konteyneri kaldırın: `docker rm -f <ad>`; ad, hata
mesajındaki `deerx-sbx-…`.

## Web sunucusu

### Loopback dışı bir adreste başlamayı reddediyor

**Belirti.** `deerx serve --host 0.0.0.0` başlamak yerine çıkıyor.

**Sebep.** Bilinçli. Kullanıcı hesabı yokken loopback dışı bir adrese bağlanmak,
komut çalıştırabilen ve dosya yazabilen bir ajanı kimlik doğrulamasız yayına
almak olurdu.

**Çözüm.** Önce kullanıcı oluşturun, sonra bağlanın:

```bash
deerx user add <ad>
```

Komut parolayı sorar; parola hiçbir zaman komut satırından alınmaz — orada kabuk
geçmişine düşerdi.

### Burada erişiliyor, başka makineden erişilmiyor

**Belirti.** Sunucu bu makinede cevap veriyor, başka bir makineden zaman aşımına
uğruyor.

**Sebep.** Üç bağımsız kapı var ve genelde şüphelendiğiniz kapı değildir: bağlama
adresi, Windows güvenlik duvarı ve — adres ağınızda değilse — yönlendirici.

**Çözüm.** Sırayla:

1. Bağlama adresinin `127.0.0.1` değil `0.0.0.0` olduğunu doğrulayın. Loopback
   bağlama başka her yerden görünmezdir; aynı makinedeki bir konteynerden bile.
2. Portu Windows Defender Güvenlik Duvarı'nda, ağın gerçekten kullandığı profil
   için açın (Özel mi Genel mi, fark eder).
3. Diğer makineden önce portun kendisini sınayın, uygulamayı suçlamadan önce.

Bir konteyner Windows konağına `localhost` üzerinden değil,
`host.docker.internal` üzerinden ulaşır — konteyner içinde `localhost`
konteynerin kendisidir.

### Her istekte oturum kayboluyor

**Belirti.** Giriş yapıyorsunuz ve sonraki istek yine kimliksiz.

**Sebep, ölçüldü.** Oturum çerezi `Secure` işaretliydi; bu, tarayıcının onu düz
HTTP üzerinden geri göndermemesi demek. HTTPS'te çalışıyordu, HTTP'de sessizce
yok oluyordu.

**Çözüm.** Düzeltildi: bayrak artık isteğin şemasından türetiliyor, yani HTTPS'te
açık, HTTP'de kapalı. TLS'i sonlandıran bir vekil sunucunun arkasındaysanız
şemayı ilettiğinden emin olun, yoksa sunucu düz HTTP görür.

## Teslimat

### Teslimat kapısı paketlemeyi reddediyor

**Belirti.** `deerx` teslimat arşivi üretmiyor.

**Sebep.** Hazırlık kapısı. Bir paket, işin bittiğine dair bir iddiadır; kapı bu
iddianın doğru olup olmadığını denetler — zorunlu fazlar tamam mı, zorunlu
teslimatlar yerinde mi.

**Çözüm.** Kapının bildirdiğini okuyun; genel bir hata vermek yerine neyin eksik
olduğunu adıyla söyler. Fazı tamamlayın ya da yeniden koşun.

Bundan ayrı olarak, her arşivden sır kalıplarına uyan dosyalar dışlanır.
Pakette beklediğiniz bir dosya yoksa kimlik bilgisine benzeyip benzemediğine
bakın — o dışlama yapılandırılabilir değil, bilerek öyle.

## Windows

### Koşuyu durdurunca arkada süreç kalıyor

**Belirti.** Koşuyu durduruyorsunuz ve bir Python süreci çalışmaya devam ediyor.

**Sebep, ölçüldü.** `CTRL_BREAK_EVENT` bir sürece değil **konsola** teslim edilir.
`CREATE_NEW_PROCESS_GROUP` grubu ayırır ama konsolu ayırmaz; bu yüzden bir çocuğa
gönderilen kesme ebeveyne ulaşabilir — ya da hiçbir şeye ulaşmayabilir.

**Çözüm.** Elle sinyal göndermek yerine yönetim betiklerini kullanın. Kapatma
yolu süreç ağacını dolaşır (`taskkill /F /T`); bir derleme aracının doğurduğu
toruna gerçekten ulaşan budur.

### Her komutta bir konsol penceresi yanıp sönüyor

**Belirti.** Ajan çalışırken siyah pencereler açılıp kapanıyor.

**Sebep.** Her çocuk süreç kendi konsolunu alıyordu.

**Çözüm.** Düzeltildi: çocuk süreçler artık `CREATE_NEW_PROCESS_GROUP` ile
birlikte `CREATE_NO_WINDOW` ile oluşturuluyor. Hâlâ görüyorsanız kendi konsolunu
açan bir araçtan geliyordur — bayrak DeerX'in doğurduğu süreçlere uygulanır, o
süreçlerin doğurduklarına değil.

## Testler

### `ModuleNotFoundError: deerx`

**Belirti.** `pytest` paketi içe aktaramıyor; `python -m pytest` çalışıyor.

**Sebep.** `python -m pytest` bulunulan dizini `sys.path`'e ekler; `pytest`
konsol betiği eklemez.

**Çözüm.** Projenin kendi denetim betiğini kullanın; lint ve testleri her ortamda
aynı biçimde koşar:

```bash
bash scripts/check.sh
```

`--fast` yavaş testleri atlar. Windows'ta `scripts/check.ps1` aynı betik.
`.githooks/` içindeki pre-push kancası bunu koşar, yani yerelde düşen şey
push'ta düşecek olan şeydir.

### Bir kod değişikliğinden sonra dokümantasyon testi düşüyor

**Belirti.** Bir araç ya da test ekliyorsunuz ve `tests/test_docs.py` düşüyor.

**Sebep.** Tasarım gereği. Dokümantasyondaki birkaç sayı koda bağlı: toplam araç
sayısı, rol başına araç sayıları ve test sayısı. Sekiz kopyası olan elle tutulan
bir sayı kendi başına doğru kalamaz — Türkçe README bir keresinde 997 test
varken 558 diyordu.

**Çözüm.** Hatanın adını verdiği sayıyı güncelleyin. Mesaj dosyayı, bulduğu
metni ve kodun bildirdiği değeri söyler.

Aynı dosya iki dilin **aynı başlık iskeletine** sahip olmasını da şart koşar. Bir
dile bölüm eklerseniz diğerine de ekleyin.

## Hiçbiri işe yaramadıysa

Sormadan önce şu üçünü toplayın:

- Düşen koşunun olay günlüğü: `<calisma-alani>/.deerx/events.jsonl`
- Sırları çıkarılmış hâliyle etkin yapılandırma
- Ne olmasını beklediğiniz ve ne olduğu

[Doğrulama durumu](verification.md) neyin koşularak doğrulandığını ve neyin
doğrulanmadığını kaydediyor. Belirtiniz "doğrulanmadı" sütunundaysa bu da bir
bilgidir — bu yolu henüz kimsenin ölçmediği anlamına gelir.
