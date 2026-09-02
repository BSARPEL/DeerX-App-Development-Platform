# Ajan araçları

[← Dokümantasyon](README.md) · [English](../tools.md)

Ajanlar serbest metinle cevap vermez — araçlarla hareket eder ve bulguları
yapılandırılmış veri olarak kaydedilir. 39 araç var; her ajan rolü dar bir alt
küme alır.

## Araç kümeleri

| Rol | Araç | Tur bütçesi | Dosya oku | Dosya yaz | Kabuk | Servis | Tarayıcı | Web |
|---|--:|--:|:--:|:--:|:--:|:--:|:--:|:--:|
| Analist | 13 | 30 | ● | | | | | |
| Araştırmacı | 14 | 35 | | | | | ● | ● |
| Değerlendirici | 11 | 30 | ● | | | | | |
| Mockup | 10 | 30 | ● | | | | | ● |
| Mimar | 11 | 35 | ● | | | | | |
| Planlayıcı | 8 | 25 | ● | | | | | |
| Backend | 14 | 45 | ● | ● | ● | ● | | |
| Frontend | 21 | 45 | ● | ● | ● | ● | ● | |
| QA | 23 | 45 | ● | ● | ● | ● | ● | |
| İnceleyici | 10 | 35 | ● | | ● | | | |
| Staging | 19 | 40 | ● | ● | ● | ● | ● | |
| Canlı | 10 | 30 | ● | | ● | | | |

Her rol ayrıca `search_knowledge` ve `read_project_state` alır.

Tur bütçesi rolün kendi tavanıdır; ajanın gerçekte aldığı
`min(rol bütçesi, max_iterations)`. Varsayılan `max_iterations = 40` ile 45
turluk üç rol 40'a kırpılır, yani gerisini açan şey o ayarı yükseltmektir.

Dar bir araç kümesi token maliyetini düşürür ve yanlış araç seçimini engeller.
Tabloyu *yokluklar* için okuyun, çünkü tasarım orada:

- **Canlı dosya yazamaz.** İncelenmiş olanı dağıtır, onu yazmaz.
- **Mockup da dosya yazamaz** — ekranları `save_artifact` ile üretir, yani
  yaptığı her şey ağacın bir yerinde başıboş bir dosya değil, izlenen bir
  çıktıdır.
- **Backend'in tarayıcısı yok.** Sunucu kodu yazar, bir servisi çalıştırıp
  günlüğünü okuyabilir; görsel doğrulama Frontend, QA ve Staging'in işidir.
- **Açık web'e yalnızca Araştırmacı ve Mockup ulaşır**, Mockup da yalnızca
  görsel için: slayta koyacağı bir fotoğrafı bulup indirebilir, ama arama
  yapamaz, sayfa okuyamaz. Değerlendirici ve Mimar zaten indekslenmiş olandan
  çalışır — araştırma gerekiyorsa o, araştırma fazının işidir ve bulguları
  kayıt olarak gelir.
- **İnceleyici komut çalıştırabilir ama yazamaz.** Okuyarak ve koşturarak
  denetler; yazabilen bir inceleyici kendi işini incelemiş olurdu.

## Bilgi tabanı

| Araç | Ne yapar |
|---|---|
| `search_knowledge` | İndekslenmiş dokümanlarda ve kodda hibrit arama (anlamsal + BM25) |
| `read_document` | İndekslenmiş bir dokümanı sırayla okur, tamamını ya da parça aralığını |
| `ingest_source` | Bir dosyayı veya dizini indeksler |
| `list_knowledge` | Dokümanları ve istatistikleri listeler |

Ajanlara **bir şey varsaymadan önce** `search_knowledge` kullanmaları ve birkaç
dar sorgunun tek geniş sorgudan iyi sonuç verdiği söylenir.

## Dosya sistemi

| Araç | Ne yapar |
|---|---|
| `read_file` | Satır numaralı okur; büyük dosyalar için `offset`/`limit` |
| `write_file` | Dosyayı tamamen yazar, üst dizinleri oluşturur |
| `edit_file` | Tam eşleşmeli değişim; `old_string` tekil olmalı |
| `list_dir` · `glob_files` · `grep_files` | Gezinme ve arama |

Her yol çalışma alanına göre çözülür ve dışarı çıkıyorsa reddedilir. `edit_file`
tekil olmayan bir eşleşmede hangisini kastettiğinizi tahmin etmek yerine
reddeder.

## Bulgu kaydı

| Araç | Anahtar biçimi |
|---|---|
| `record_requirements` | `REQ-001` |
| `record_questions` | `Q-001` |
| `record_gaps` | `GAP-001` |
| `record_decisions` | `ADR-001` |
| `record_research` | — |
| `record_tasks` | `T-001` |
| `update_task` · `save_artifact` | — |

Aynı anahtarı tekrar yazmak kaydı günceller. Gereksinimler dokümandaki bir
dayanağa (`source_ref`) bağlanmalıdır; dayanağı olmayan bir çıkarım
`category="assumption"` olarak işaretlenmek zorundadır.

## Kabuk

`run_command` çalışma alanında bir komut çalıştırır ve stdout/stderr döner.

Sırayla üç kapıdan geçer:

1. **Reddetme listesi** — yıkıcı desenler, koşulsuz reddedilir. Eşleştirme
   konum duyarlıdır: `srv.shutdown()` ve `--shutdown-timeout`, `shutdown`
   komutu değildir ve bunları engellemek bir hataydı.
2. **İzin listesi** — `deerx.toml` içindeki `[deerx.shell] allow_prefixes`.
   Çıplak komut adları yalnızca komut konumunda eşleşir.
3. **Onay** — `approval_mode = "ask"` ile komutu çalışmadan önce görürsünüz.

Bilinmesi gereken iki davranış:

- **Zaman aşımı tüm süreç ağacını öldürür.** `subprocess.run(timeout=…)`
  yalnızca kabuğu öldürür; çocuklar boruları açık tutarak yaşamaya devam eder ve
  `communicate()` komutun tam süresi kadar bloke olur. Ölçüldü: 2 saniyelik
  sınırla 30 saniyelik bir komut 30 saniye sürdü.
- **Çok satırlı komutlar bir betiğe yazılır.** Windows'ta `cmd.exe` yeni satırı
  komut sonlandırıcı sayar, dolayısıyla çok satırlı bir komut ilk satırını
  koşup çıkış kodu 0 dönüyor ve gerisi sessizce düşüyordu. Artık çok satırlı
  komutlar geçici bir dosyaya yazılıp POSIX kabuğuyla çalıştırılır.

## Servisler — yazdığını çalıştırmak

Bir ajanın kendi işini test edebilmesini sağlayan şey budur.

| Araç | Ne yapar |
|---|---|
| `start_service` | Arka planda bir süreç başlatır; koşu boyunca ayakta kalır |
| `service_log` | Çıktısını okur |
| `stop_service` · `list_services` | Durdurur / çalışanları listeler |

**Neden `run_command`'dan ayrı.** `run_command` komutun bitmesini bekler ve
zaman aşımında ağacı öldürür — testler için doğru, dev sunucusu için yanlış.
Ölçüldü: `python srv.py` sekiz saniyede öldürüldü; `python srv.py &` Windows'ta
`&` komut ayıracı olduğu için yine bloke edip öldürüldü; `start /b` izin
listesinde olmadığı için reddedildi. Ajanın uygulamasını iki araç çağrısı
arasında ayakta tutmasının hiçbir yolu yoktu.

`start_service` süreci kopuk başlatır ve çıktısını bir **dosyaya** yazar. Boru
kullanılırsa ebeveyn okumaya devam etmek zorundadır; asıl bloke eden odur.

**Port verirseniz beklenir** — o port dinlemeye başlayana kadar dönülmez, yani
"başlattım" demek "gerçekten hazır" demek olur. Süreç hemen ölürse günlüğünün
sonu hata olarak döner.

Servisler koşuya bağlıdır ve koşu bitince hepsi kapanır. Portu tutmaya devam
eden yarım kalmış bir dev sunucusu bir sonraki koşuyu "port dolu" ile karşılar
ve sebebi görünmez olur.

```
start_service(command="npm run dev", port=3000, name="web")
```

## Tarayıcı — yaptığını görmek

| Araç | Ne yapar |
|---|---|
| `preview_open` | Yerel uygulamanızı sunucudaki Chrome'da açar |
| `browser_snapshot` | Tıklanabilir/yazılabilir öğeleri `ref` numaralarıyla listeler |
| `browser_click` · `browser_type` · `browser_back` | Sayfayı kullanır |
| `browser_console` | Sayfanın **kendi** hataları: konsol, düşen istek, 4xx/5xx |
| `browser_screenshot` | Görüntüyü çıktı olarak kaydeder **ve modele gösterir** |
| `browse_page` · `web_search` · `fetch_url` | Açık web'de araştırma |

**`browser_console` neden isteğe bağlı değil.** Anlık görüntü sayfanın nasıl
*göründüğünü* söyler, çalışıp çalışmadığını değil. Bir düğme tam yerinde durup
tıklanınca istisna atabilir. Araç okumadan önce ağın oturmasını bekler —
ölçüldü, bir resmin 404'ü sayfa yüklendikten 1,2 saniye sonra hâlâ gelmemişti ve
2 saniye içinde gelmişti. Kayıt her `preview_open`/`browse_page` ile sıfırlanır,
böylece önceki sayfanın hatası bu sayfanınki sanılmaz.

`browser_snapshot` ham HTML yerine numaralı bir öğe listesi döner: hem token
olarak daha ucuz, hem üzerinde işlem yapmak için daha güvenilir.

**Ekran görüntüsü yalnızca dosyalanmıyor, modele gönderiliyor.** Eskiden diske
yazılıp modele yalnızca "kaydedildi" dönüyordu; ajan sayfanın *yapısını*
biliyordu ama *görünüşünü* bilmiyordu — hizalama bozukluğu, üst üste binen
kutular, kırpılmış görsel ve okunmayan metin döngüsünün dışındaydı. Yerel vLLM
ucunda ölçüldü: model, ekran görüntüsüne yazılmış rastgele bir kodu aynen okudu;
yani görüyor. İki kısıt biçimi belirliyor: OpenAI biçiminde `role: "tool"`
mesajları görsel taşıyamaz, bu yüzden görüntü araç sonuçlarının ardına eklenen
bir `user` mesajında gider; ve her model görmez, bu yüzden uç görüntüyü
reddederse istemci bunu bir kez öğrenir, **geçmişteki** görüntüleri de temizler
— orada kalan bir görüntü sonraki her turda aynı reddi üretirdi — ve isteği
yeniden dener. Ajan hiçbir şey fark etmez, yalnızca eski davranışa döner. 4 MB
üstü görüntüler gönderilmez, kayda geçer: base64 bir görüntüyü üç kat büyütür ve
birkaç tanesi bağlamı doldurur.

`preview_open` yalnızca `127.0.0.1:<port>` kabul eder, izin sunucu tarafında
verilir ve koşu bitince düşer. Modelin politika listesine doğrudan erişimi yok.

QA yönergesi bunu kabul ölçütü sayar: ana akış, boş durum ve bir hata durumu
denenip ekran görüntüsü bırakılmadan faz bitmiş sayılmaz.

## Web araştırması

`web_search` sunucudaki Chrome'u kullanır. Özetler karar vermeye yetmez —
yönerge, ardından `browse_page` ile sonucu gerçekten okumaktır.

Boş sonuç ajana "sonuç yok" olarak değil, **hata** olarak bildirilir. Fark
önemlidir: başarısız bir aramayı "böyle bir şey yok" diye okuyan bir model bunu
rapora emin bir dille yazar.

`fetch_url` bir sayfayı indirir, metnini çıkarır ve **indeksler**; böylece
sonraki fazlar onu `search_knowledge` ile bulabilir. Özel, loopback ve
link-local adresleri reddeder (SSRF) ve kontrolü ad çözümlemesinden **sonra**
yapar, ana makine adına güvenmez.

## Görseller — slayt ve mockup'lar için

| Araç | Ne yapar |
|---|---|
| `find_images` | Web'de görsel arar; adres, boyut ve **kaynak** döner |
| `download_image` | Birini çalışma alanına indirir, çıktı olarak kaydeder |

`fetch_url` bunu yapamaz: `response.text` okur, ikili veri taşımaz. Bu ikisi
olmadan mockup rolünün elinde yalnızca CSS ile çizilmiş kutular kalıyordu.

**Burada tasarımı belirleyen kısıt lisanstır.** Rastgele bir web fotoğrafını
birinin teslimatına koymak telif riski üretir. Tek bir SearXNG görsel sorgusu on
bir motordan sonuç döndürüyor — ölçüldü — ve lisansı bilinenlerle (Openverse,
Wikimedia Commons, Unsplash, Pexels) bilinmeyenleri (Bing, Google, Pinterest)
birlikte getiriyor. Bu yüzden `find_images` varsayılan olarak yalnızca lisansı
bilinenleri getirir, her sonucu geldiği motorla etiketler ve serbest lisanslı
sonuç yoksa **sessizce diğerlerine düşmek yerine bunu söyler**; çünkü bir ajanın
lisanslı sandığı bir görseli teslim etmesi tam olarak öyle olur.
`download_image` kaynak adresini çıktı özetine yazar, sonradan atıf verilebilsin
diye.

Bir kaynağın serbest olması yetmez, **indirilebilir** de olmalı: Art Institute
of Chicago bilerek listede yok, çünkü IIIF ucu beş denemenin beşinde de 403
döndü — kimlikli istekte bile. Listede kalsaydı sıralamada üste çıkıp her
seferinde bir tur harcatırdı.

İçerik türüne değil baytlara bakılır: sunucu 200 dönüp gövdede hata sayfası
verebilir ve bunu kaydetmek slaytta sebebi anlaşılmayan kırık bir görsel olarak
görünürdü.

## Modele ne söyleniyor

Araç açıklamaları modele giden metindir ve iki dillidir: Türkçe metin araç
sınıfının içinde (davranışın belgelendiği yer), İngilizcesi
`tools/descriptions_en.py` içinde, ve `Tool.spec()` geçerli dile göre üstüne
biner. Bir test her aracın ve açıklamalı her parametrenin ikisinde de karşılığı
olduğunu doğrular — yeni bir araç tek dilli olarak yayımlanamaz. Bkz.
[İki dilli mimari](i18n.md).

## İş akışı danışmanı

Üç araç yalnızca bir iş akışı sohbetinin içinde bulunur (`deerx chat`,
`POST /api/workflows/{id}/chat`, `deerx_workflow_chat` MCP aracı).

| Araç | Ne yapar |
|---|---|
| `read_workflow` | Konuşulan iş akışının hedefi, talimatı, koşuları, çıktıları ve planları |
| `update_workflow` | O iş akışının başlığını, hedefini ya da talimatını değiştirir |
| `resolve_question` | Açık bir soruyu kullanıcının cevabıyla kapatır, ya da belirtilen varsayımla atlar |

Hiçbiri iş akışı argümanı almaz. Kapsam modelden değil çağırandan gelir:
kimliği argüman yapmak, modele *hangi* iş akışını değiştireceğini
sormaktır ve yanlış bir sayı üretmesi yanlış olanı düzenlemesine yeter.
## Ayrıca

- [Güvenlik modeli](security.md) — kabuk politikası ve hapsetme nasıl çalışıyor
- [Boru hattı](pipeline.md) — hangi ajan ne zaman koşuyor
- [Mimari](architecture.md) — araç katmanı nerede duruyor
