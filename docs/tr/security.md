# Güvenlik modeli

[← Dokümantasyon](README.md) · [English](../security.md)

Politika ve güvenlik açığı bildirimi için [SECURITY.md](../../SECURITY.md)
dosyasına bakın. Bu sayfa teknik ayrıntıdır.

## Sınır süreç değil, dosya araçları

DeerX komutları konteynerde ya da sanal makinede değil, **doğrudan makinede**
çalıştırır. Kısıtlanan şey dosya araçlarının görebildiği dizin ve kabuk aracının
hangi komutları çalıştıracağıdır.

Yani:

- İzin verilen bir komut, o komutun yapabildiği her şeyi yapabilir — çalışma
  alanının dışına ve ağa çıkmak dahil.
- Hapsetme *ajanın dosya araçlarının* dolaşmasını engeller. Ajanın başlattığı
  *süreçleri* kum havuzuna almaz.

Gerçek yalıtım gerekiyorsa DeerX'i bir konteyner içinde çalıştırın. Bu açıkça
yazılıyor çünkü okunmak yerine varsayılması en muhtemel şey bu. Deponun
`Dockerfile` dosyası o imajı üretir — `docker build -t deerx .`, ardından
sunucu yayınlanan bir porta bağlanmadan önce bir hesap oluşturun; tam sıra
dosyanın başlığındaki yorumlarda yazılı.

Bunun aşağıdaki `execution = "docker"` ayarından **farklı** bir mekanizma
olduğuna dikkat edin. İmaj **DeerX'in tamamını** konaktan yalıtır; `execution`
ise DeerX'i konakta bırakıp yalnızca ajanın komutlarını yalıtır. Birini seçin.

## Yol hapsi

Ajanın verdiği her yol genişletilir, çözülür ve kontrol edilir:

```python
resolved = candidate.resolve()
if not resolved.is_relative_to(workspace.resolve()):
    raise WorkspaceError(...)
```

Çözümleme kontrolden **önce** yapılır, yani `../`, sembolik bağlar ve mutlak
yollar önce gerçek bir konuma indirgenir. Dışarı düşen bir yol, iki yol da
adıyla anılarak reddedilir; böylece ajan körlemesine tekrar denemek yerine
kendini düzeltebilir.

## Kabuk politikası

Sırayla üç kapı:

### 1. Reddetme listesi — koşulsuz

Yıkıcı desenler izin listesinden bağımsız olarak reddedilir.

Eşleştirme **konum duyarlıdır**. Daha önceki bir alt dizi eşleştirmesi meşru
kodu reddediyordu: `srv.shutdown()`, `sock.shutdown()`, `--shutdown-timeout` ve
hatta `print('reboot notu')` — hepsi bir yerinde yasaklı bir sözcük geçtiği için
engelleniyordu. Çıplak bir komut adı artık yalnızca komut konumunda eşleşir.

### 2. İzin listesi

`deerx.toml` içindeki `[deerx.shell] allow_prefixes`. Ayrıştırma tırnak
duyarlıdır, yani `python -c "import sys; sys.exit(1)"` tek bir `python`
komutudur, bir `sys.exit` enjeksiyonu değil.

Boş bir liste yalnızca reddetme listesinin uygulanması demektir. Bu gerçek
sonuçları olan gerçek bir tercihtir — bkz. [Yapılandırma](configuration.md).

### 3. Onay

`approval_mode = "ask"` (varsayılan) ile komutu çalışmadan önce terminalde ya da
tarayıcıda görürsünüz. Onaylar koşu boyunca imza başına hatırlanır, aynı komut
iki kez sorulmaz.

`start_service` de aynı üç kapıdan geçer. Uzun ömürlü bir süreç başlatmak tek
seferlik bir komuttan daha az tehlikeli değildir.

### Zaman aşımı ağacı öldürür

`subprocess.run(timeout=…)` yalnızca kabuğu öldürür. Çocukları boruları açık
tutarak yaşamaya devam eder ve `communicate()` komutun gerçek süresi kadar
bloke olur. Ölçüldü: 2 saniyelik sınırla 30 saniyelik bir komut 30 saniye sürdü
— zaman aşımı hiçbir şey yapmadı.

Kabuk aracı komutları kendi süreç gruplarında başlatır ve zaman aşımında grubun
tamamını öldürür.

## Yalitilmis calistirma (istege bagli)

Varsayilan olarak ajanin `run_command` ve `start_service` cagrilari **konak
makinede** kosar ve kabuk izin listesiyle cevrilidir. Bu cit makineyi korur
ama ajanin mesru islerini de engeller: yanlislikla yarattigi bir dosyayi
silemez, cunku `rm` listede yoktur.

`execution = "docker"` derseniz ikisi de tek kullanimlik bir konteynerde
kosar:

```toml
[deerx]
execution = "docker"
sandbox_image = "python:3.13-slim"
sandbox_port_base = 8100
sandbox_port_count = 10
```

Konteynerde **izin listesi uygulanmaz** — korunacak konak yoktur ve patlama
yaricapi kosu bitince silinen bir konteynerdir. Ajan `rm` calistirir, paket
kurar, surec oldurur.

Uc kisit, ucu de olculdu (Windows, Docker 29.7.2):

* `--network host` konteyner portunu Windows konagina **acmaz**. Bu yuzden
  portlar konteyner kurulurken *yayinlanir* ve servis bu araliktan secilmek
  zorundadir; Docker yayinlanan portu sonradan ekleyemez.
* Konteyner icindeki servis `0.0.0.0` adresine baglanmali, `127.0.0.1`e
  degil; yoksa yayinlanan port bos kalir.
* "Port bos mu / hazir mi" denetimi konteynerin **icinden** yapilmali.
  Konaktan bakildiginda yayinlanan her port zaten acik gorunur, cunku
  dinleyen Docker'in kendisidir; konaga sormak, servis hic baslamamisken
  "hazir" derdi.

Yalitim bilerek tam degildir: **calisma alani baglanir**, boylece ajan ile
konak tarafli dosya araclari ayni dosyalari gorur. Is akisini calistiran sey
budur -- ve `rm -rf /` yine projeyi silerdi. Felaket kaliplari her iki kipte
de reddedilir.

Konteynerin aldigi iki sey daha, ikisi de olculdu:

* **Kaynak sinirlari.** `--memory 2g`, `--cpus 2`, `--pids-limit 512`.
  Olmasaydi bir fork bombasi ya da bellek doldurma konteynerde kalmaz,
  makineyi dizustu ederdi -- yalitimin var olma sebebi tam olarak bu.
* **Konak servislerine yol yok.** Kapatilmadan once konteyner, konaktaki
  vLLM (8008), SearXNG (8890) ve **DeerX'in kendi arayuzune** (8791)
  `host.docker.internal` uzerinden ulasabiliyordu -- ajan kendi sandbox'indan
  cikip DeerX'i surebilirdi. O ad artik konteynerin kendisine cozuluyor.
  Disari internet acik kaliyor, yani `pip` ve `apt` calisiyor. Bu tam bir ag
  bolmesi degildir; ag gecidi adresi hala yonlendirilebilir.

Varsayilan imaj `python:3.13`, `-slim` degil: olculdu, slim icinde `git`,
`curl`, `gcc` ve `make` YOK; ajan ilk derlemede ya da `git init`te duvara
carpar. Proje-ozel araclar icin `sandbox_setup` ayarini kullanin; konteyner
ilk kuruldugunda bir kez calisir.

## Ağ

### `fetch_url` ve SSRF

Özel, loopback, link-local ve multicast adresleri reddeder. Kontrol ad
çözümlemesinden **sonra**, çözülen her adres üzerinde yapılır — `127.0.0.1`
adresine çözülen bir ana makine adı yakalanır, herkese açık göründüğü için
güvenilmez.

### Tarayıcı vekili

Ajanın tarayıcısı, hem `CONNECT` hem absolute-form istekleri işleyen filtreleyen
bir vekilin arkasında çalışır. URL politikası sayfada değil orada uygulanır,
böylece bir yönlendirme ya da alt kaynak politikadan kaçamaz.

Politika bir DNS-rebinding savunması taşır: politikanın onayladığı adres,
bağlantının kullandığı adrestir.

### Yerel önizleme

`preview_open` yalnızca `127.0.0.1:<port>` kabul eder. İzin sunucu tarafında
verilir — modelin politika listesine ulaşmasının bir yolu yoktur — ve koşu
bitince düşer.

`enable_web` (internet erişimi) ve `browser_allow_preview` (loopback önizleme)
**ayrı ayarlardır**. Bir zamanlar tekti; bu, internet erişimini kapatmanın
ajanın az önce yazdığı uygulamayı açmasını da engellemesi demekti.

## Kimlik doğrulama

Bir kullanıcı var olduğu anda devreye girer. Kullanıcısız yerel bir kurulum
eskisi gibi davranır; kullanıcısız bir sunucu ise **loopback dışı bir adrese
bağlanamaz** — `--host 0.0.0.0` başlamayı reddeder. Dosya yazıp komut çalıştıran
bir uç için uyarı yeterli olmazdı.

İlk yönetici, yalnızca sunucu konsoluna basılan bir **kurulum jetonuyla**
oluşturulur; böylece sunucuya önce ulaşan biri hesabı sahiplenemez.

### Parolalar

Kullanıcı başına ayrı tuzla `scrypt` — bellek-zor ve standart kütüphanede, yani
yeni bağımlılık yok.

Politika NIST SP 800-63B'ye uyar: asgari **8 karakter**, kompozisyon kuralı yok.
Büyük harf, rakam ve simge dayatmak insanları `Parola1!` gibi kalıplara iter.
Onun yerine bilinen parola listesi kontrol edilir ve listedeki bir parola
reddedilmek yerine **uyarıyla kabul edilir** — kendi makinesinde kendi hesabını
kuran bir yöneticiye "hayır" demek paternalizm olurdu, riski gizlemek ise daha
kötü.

### Kararlar ve gerekçeleri

| Karar | Neden |
|---|---|
| Hata mesajı kullanıcı adıyla parolayı ayırmaz | Hangisinin yanlış olduğunu söylemek kullanıcı sayımına yarar |
| Olmayan kullanıcıda da KDF çalışır | Yanıt süresi "bu hesap var mı" sorusunu cevaplamasın |
| 8 başarısız denemeden sonra 5 dakika kilit | Hesap bazlı, yani biri başkasını kilitleyemez |
| Çerez `HttpOnly` + `SameSite=Lax`, istek HTTPS ile geldiyse `Secure` | XSS ile çalınamaz, siteler arası POST'a takılmaz, önde TLS varsa açık metin gitmez |
| Parola değişince tüm oturumlar düşer | Var olan oturumları kesmek parolayı değiştirmenin amacıdır |
| Yanlış parolada hesabın kapalı olduğu söylenmez | Aksi halde parola bilinmeden hesap durumu sızardı |
| Ana yönetici silinemez, rolü düşürülemez, kapatılamaz | Son yönetici herkesi dışarıda bırakamasın |
| Zorlama ara katmanda, rota başına değil | Yeni bir uç eklemeyi unutmak mümkün olmasın |

Oturumlar tam da iptal edilebilsinler diye sunucu tarafında tutulur. Çalınan bir
parolanın açık oturumlarının anında kapatılması gerekir ve durumsuz bir jeton
kapatılamaz.

## Denetim günlüğü

Sunucu dosya yazıyor ve kabuk komutu çalıştırıyor. Ortak bir kurulumda "bunu kim
yaptı" sorusunun bir cevabı olmalı, ve bu cevabın sonradan verilebilmesi için
olay **olurken** yazılmış olması gerekir.

`GET /api/audit` günlüğü döner; Ayarlar ekranı gösterir. Yalnızca yöneticiye —
hiç hesap yoksa herkese, çünkü o kurulumda sunucunun tamamı zaten açıktır ve
günlüğü tek başına kapatmak hiçbir şey korumazken tek kullanıcılı kurulumda
paneli ölü bırakırdı.

| Yazılan | Tutulan ayrıntı |
|---|---|
| `login`, `logout`, `login.failed` | Denenen ad, adres, tarayıcı |
| `run.start`, `run.stop` | Koşunun başlığı, çevrilebilir anahtar olarak |
| `settings.change` | Değişen alanların **adları** |
| `user.*`, `password.*` | İşlemin dokunduğu hesap |
| `package.build`, `knowledge.*` | Dosya ya da kaynak |

Söylenmeye değer dört karar:

| Karar | Neden |
|---|---|
| Reddedilen girişler, denenen adla birlikte yazılır | "Bilinmeyen bir hesaba on deneme" bir güvenlik günlüğünün en çok işe yarayan satırıdır, ve atlanması en kolay olanıdır — tutunacak bir `User` nesnesi yok |
| Ayar değişikliğinde alan adları yazılır, değerler asla | Değerlerin arasında API anahtarları var. Koruduğu şeyi sızdıran bir günlük kendi amacının karşısına geçer |
| Silinen hesabın izi kalır, yalnızca bağı kopar | Aksi halde hesabı silmek, geçmişi temizlemenin yolu olurdu |
| Günlüğün bir tavanı var, ve budama aralıklı | Proje veritabanıyla aynı dosyayı paylaşıyor. Her yazmada budamak her girişe 5000 satırlık bir tarama yüklerdi; bu yüzden 256 satırda bir çalışır — satır sayısı büyümeyi durdurur, sadece tam çizgide durmaz |

Günlük adresleri ve tarayıcı bilgilerini tutuyor. Onu faydalı kılan da bu,
kısıtlanmaya değer kılan da: DeerX'te okumanın kendisinin bir ayrıcalık olduğu
birkaç yerden biri.

## Sırlar

| Nerede | Ne geçerli |
|---|---|
| Diskte | Çalışma alanındaki `.env`; gitignore'da, yalnızca `.env.example` takipli |
| API üzerinden | Yalnızca yazma. Ayarları okumak `has_*` boolean'ları döner, değer değil |
| Teslimat paketlerinde | Desenle dışlanır ve **manifestoda adıyla anılır** |
| Depoda | Ne çalışma ağacında ne git geçmişinde hiçbir anahtar var |

Teslimat dışlaması `.env`, `*.pem`, `*.key`, `id_rsa*`, `credentials*`,
`service-account*.json` ve benzerlerini kapsar, `.env.example` korunur.
Desenler yolun **her** parçasına uygulanır, yani bir monorepo'daki
`frontend/node_modules/` kökteki kadar dışlanır.

Dışlanan her sır manifestoda `DAHIL EDILMEDI` olarak listelenir — görünür,
sessiz değil. Bir test, üretilen zip'in ham baytlarında hiçbir sır değerinin
geçmediğini doğrular.

## İşleme

- Çıktı markdown'ı ham HTML enjeksiyonu kapalı işlenir.
- HTML mockup'lar `sandbox` iframe içinde gösterilir.
- API hata metni tarayıcıya veri olarak ulaşır, biçimlendirme olarak değil.

## Bilerek savunulmayanlar

- **İndekslenmiş içerik üzerinden prompt enjeksiyonu.** Şartnameler, kaynak
  yorumları ve çekilen web sayfaları modelin okuduğu şeylerdir. Modele ulaşan
  metin onu yönlendirmeye çalışabilir. Bir çalışma alanını güvenmediğiniz
  içeriğe yönlendirmeyin.
- **`approval_mode = "auto"` ayarlayan bir operatör** gözetimsiz çalıştırma
  alır. Ayarın amacı budur.
- **İzin verilen bir komutun yaptığı hiçbir şey.** Karar noktası izin
  listesidir; ondan sonra komut komuttur.

## Ayrıca

- [SECURITY.md](../../SECURITY.md) — politika ve özel bildirim
- [Yapılandırma](configuration.md) · [Ajan araçları](tools.md) · [Teslimat paketleri](delivery.md)
