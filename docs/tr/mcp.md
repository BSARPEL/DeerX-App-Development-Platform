# MCP sunucusu

[← Dokümantasyon](README.md) · [English](../mcp.md)

DeerX bilgi tabanını ve boru hattını [Model Context
Protocol](https://modelcontextprotocol.io) üzerinden açar; böylece başka bir
ajan — Claude Code, Cline ya da MCP konuşan herhangi bir şey — onu araç olarak
kullanabilir.

## Yapılandırma

```json
{
  "mcpServers": {
    "deerx": {
      "command": "uv",
      "args": ["run", "--directory", "/yol/DeerX-App-Development-Platform", "deerx-mcp"],
      "env": {
        "DEERX_WORKSPACE": "/yol/hedef-proje",
        "ANTHROPIC_API_KEY": "sk-ant-...",
        "DEERX_APPROVAL_MODE": "auto"
      }
    }
  }
}
```

`DEERX_WORKSPACE` sunucunun hangi projeye hizmet edeceğini belirler. `uv`
olmadan:

```json
{ "command": "python", "args": ["-m", "deerx.mcp_server"] }
```

`.env` geçerli dizinden değil **çalışma alanından** okunur — aksi halde bu
şekilde başlatılan bir sunucu projenin kendi anahtarını sessizce görmezden
gelirdi.

`DEERX_APPROVAL_MODE=auto` burada genelde istenen şeydir: bir MCP sunucusunun
soracağı bir terminal yoktur. Ayarlamadan önce ne anlama geldiğini anlayın —
bkz. [Güvenlik modeli](security.md).

## Araçlar

| Araç | Ne yapar |
|---|---|
| `deerx_ingest` | Dosya veya dizin indeksler |
| `deerx_search` | Bilgi tabanında hibrit arama |
| `deerx_documents` | İndekslenmiş dokümanları listeler |
| `deerx_status` | Faz durumları ve sayımlar |
| `deerx_state` | Gereksinimler, boşluklar, kararlar, araştırma bulguları |
| `deerx_tasks` | Görev listesi |
| `deerx_next_task` | Bağımlılıkları tamam olan sıradaki görev |
| `deerx_update_task` | Bir görevin durumunu ve sonucunu günceller |
| `deerx_artifact` | Üretilmiş bir çıktıyı getirir |
| `deerx_run_phase` | Tek bir faz koşturur |
| `deerx_questions` | Açık sorular |
| `deerx_answer` | Birini cevaplar |
| `deerx_skip_question` | Bir varsayımla geçer |
| `deerx_package` | Hazırlık kapısı + teslimat zip'i |

## Kaynaklar

| URI | |
|---|---|
| `deerx://state` | Proje hafızası, yapılandırılmış veri olarak |
| `deerx://artifacts/{ad}` | Tek bir çıktı |

## MCP üzerinden soru kapısı

`deerx_run_phase` bloke eden bir soruya takıldığında şunu döner:

```json
{ "status": "needs_input", "questions": [ ... ] }
```

**Dışarıdaki ajan soruları `deerx_questions` ile okuyup kullanıcıya iletmeli —
kendisi cevaplamamalı.** Bloke eden bir sorunun bütün amacı, hiçbir akıl
yürütmenin üretemeyeceği bir şeyi istemesidir. Kullanıcı adına cevaplayan bir
ajan, kapının tam olarak engellemek için var olduğu yanlış varsayımı geri
getirir — ve bunu görünmez biçimde yapar, çünkü kaydedilen cevap artık
kullanıcınınki gibi görünür.

## İkili çıktılar

`deerx_artifact` bir `.zip` üzerinde ham bayt yerine paketin `TESLIMAT.md`
raporunu döner — web arayüzünün izlediği kuralın aynısı. Bir modele arşiv
baytları vermek işe yarar hiçbir şey üretmez ve çok sayıda token harcar.

## İki ajan, tek çalışma alanı

MCP sunucusuyla bir `deerx serve`'ün aynı çalışma alanını göstermesini
engelleyen bir şey yok. SQLite proje hafızasını paylaşırlar ve o eşzamanlı
erişimi kaldırır — ama vektör önbelleği tam da bir zamanlar kaldırmadığı için
süreçler arası geçersizleştirilir: bir süreçten indekslenen doküman diğerinde
anlamsal aramada görünmüyordu.

Tek bir çalışma alanına karşı aynı anda iki boru hattı **koşusu** çalıştırmayın.
Web koşucusu bu yüzden eşzamanlı koşuyu reddeder; MCP sunucusunun başka yerde
başlatılmış bir koşuyu görme imkânı yoktur.

## Ayrıca

- [CLI referansı](cli.md) — `deerx mcp`
- [Yapılandırma](configuration.md) — `DEERX_WORKSPACE` ve `.env` çözümlemesi
- [Güvenlik modeli](security.md) — `approval_mode = "auto"` neyi feda ediyor
