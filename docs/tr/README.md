# DeerX dokümantasyonu

[← Proje README'sine dön](../../README.tr.md) · [English](../README.md)

## Buradan başlayın

| | |
|---|---|
| **[Başlangıç](getting-started.md)** | Kurulum, sağlayıcı yapılandırması, ilk koşu |
| **[Boru hattı](pipeline.md)** | 13 faz, ajan kadrosu, şerit yönlendirmesi, soru kapısı |
| **[Model sağlayıcıları](providers.md)** | Yerel vLLM, Ollama, OpenAI, Anthropic — ve gerçek farkları |

## Kullanım

| | |
|---|---|
| **[Web arayüzü](web-ui.md)** | Her ekran, ne gösterdiği ve neden öyle düzenlendiği |
| **[CLI referansı](cli.md)** | Her komut, bayrakları, çıkış kodları ve yönetim betikleri |
| **[Yapılandırma](configuration.md)** | `deerx.toml`, ortam değişkenleri, öncelik sırası, dil |
| **[Teslimat paketleri](delivery.md)** | Hazırlık kapısı, sır dışlama, arşiv düzeni |
| **[MCP sunucusu](mcp.md)** | Bilgi tabanını ve boru hattını başka bir ajana açmak |
| **[Sorun giderme](troubleshooting.md)** | Gerçekten yaşanmış belirtiler, ölçülen sebepleri ve çözümleri |

## Nasıl çalışıyor

| | |
|---|---|
| **[Ajan araçları](tools.md)** | 39 aracın tamamı ve ajanın yazdığını çalıştırıp test etmesi |
| **[Mimari](architecture.md)** | Modül haritası ve her kararın arkasındaki gerekçe |
| **[Güvenlik modeli](security.md)** | Hapsetme, kabuk politikası, kimlik doğrulama, sırlar |
| **[İki dilli mimari](i18n.md)** | Tek ayarın arayüze, CLI'ye, araçlara ve yönergelere ulaşması |
| **[Doğrulama durumu](verification.md)** | Koşularak doğrulananlar — ve doğrulanmayanlar |
| **[DeerX'i genişletmek](extending.md)** | Araç, faz, sağlayıcı ya da dil eklemek |
| **[Projenin kendi bilgi tabanı](knowledge-base.md)** | DeerX'in belgelerini ve kodunu indeksleyip bir modele sorun |

## Bu dokümantasyondaki kurallar

Ölçüm olarak yazılan her şey gerçekten ölçüldü — sayılar gerçek koşulardan
geliyor ve bir sayı varsayımla çelişiyorsa varsayım adıyla anılıyor. Bir şey
doğrulan**ma**dıysa öyle yazıyor; [Doğrulama durumu](verification.md) bölümüne
bakın.

Çıktı dosya adları iki dilde de Türkçe (`analiz-raporu.md`, `mimari.md`,
`gelistirme-plani.md`). Bu bilinçli: boru hattı bir fazın teslimatını dosya
adına bakarak eşliyor, dolayısıyla çevirmek fazın gerçekten bir şey üretip
üretmediğini denetleyen kontrolü kırardı.
