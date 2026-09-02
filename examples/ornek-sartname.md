# Saha Servis Yönetim Sistemi — Şartname

> Örnek doküman. Boru hattını denemek için:
> `deerx init deneme && cp examples/ornek-sartname.md deneme/docs/ && cd deneme && deerx run`
>
> Bu şartname bilerek eksik bırakılmıştır — değerlendirme fazının ne bulduğunu
> görmek için tasarlandı.

## 1. Amaç

Teknik servis ekiplerinin iş emirlerini mobil cihaz üzerinden yönetmesi. Şu an
süreç kağıt fiş ve telefonla yürüyor; hangi teknisyenin nerede olduğu ve işin
hangi aşamada olduğu merkezi olarak görülemiyor.

## 2. Hedef kitle ve aktörler

| Aktör | Sorumluluk |
|---|---|
| Saha teknisyeni | İş emrini görüntüler, durumu günceller, fotoğraf ve imza toplar |
| Operasyon yöneticisi | İş emri açar ve atar, SLA takibi yapar, raporları görür |
| Müşteri | Talep açar, işin durumunu izler, tamamlanan işi onaylar |
| Sistem yöneticisi | Kullanıcı ve yetki yönetimi yapar |

## 3. Fonksiyonel gereksinimler

### 3.1 İş emri yaşam döngüsü

Durumlar: `açıldı → atandı → yolda → başladı → tamamlandı → onaylandı`.

- Her durum geçişi zaman damgası ve geçişi yapan aktörle birlikte saklanmalı.
- `tamamlandı` durumuna geçiş için en az bir fotoğraf ve müşteri imzası zorunlu.
- İş emri iptal edilebilmeli; iptal gerekçesi seçmeli bir listeden alınmalı.

### 3.2 Atama

Operasyon yöneticisi iş emrini bir teknisyene atar. Sistem, teknisyenin mevcut
yükünü ve konumunu dikkate alarak öneri sunmalı.

### 3.3 Çevrimdışı çalışma

Teknisyen kapsama alanı dışındayken kayıt yapabilmeli; bağlantı geldiğinde
kayıtlar otomatik senkronize olmalı.

### 3.4 Bildirimler

- Teknisyene yeni atama yapıldığında anlık bildirim.
- SLA süresinin %80'i dolduğunda operasyon yöneticisine uyarı.
- Müşteriye iş tamamlandığında bilgilendirme.

### 3.5 Raporlama

Operasyon yöneticisi şu raporları görebilmeli: teknisyen başına tamamlanan iş
sayısı, ortalama tamamlanma süresi, SLA ihlalleri, bölge bazlı yoğunluk.

## 4. Nonfonksiyonel gereksinimler

- Mobil uygulama 3 saniyeden kısa sürede açılmalı.
- Sistem 500 eş zamanlı teknisyeni desteklemeli.
- Kişisel veriler KVKK'ya uygun saklanmalı.
- Uygulama iOS ve Android'de çalışmalı.
- Sistem çalışma saatleri içinde %99,5 erişilebilir olmalı.

## 5. Entegrasyonlar

- Mevcut ERP sistemine iş emri ve stok senkronizasyonu.
- SMS sağlayıcısı üzerinden müşteri bilgilendirmesi.
- Harita servisi ile rota ve mesafe hesabı.

## 6. Kısıtlar

- Proje 4 ay içinde canlıya alınmalı.
- Mevcut ERP değiştirilemez; entegrasyon onun API'si üzerinden olacak.
- Teknisyenlerin bir kısmı düşük donanımlı Android cihaz kullanıyor.

## 7. Kapsam dışı

- Muhasebe ve faturalandırma.
- Yedek parça satın alma süreci.
