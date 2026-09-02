"""Python tarafinin mesaj katalogu.

Arayuz metinleri `static/i18n.js` icinde cift dilli. Ama kullanicinin
gordugu her sey oradan gelmiyor: canli olay akisindaki satirlar, arac
hatalari ve CLI ciktisi Python'dan geliyordu ve dilden bagimsiz olarak
Turkce'ydi. Ingilizce secili bir arayuzde akis Turkce akiyordu.

Kullanim:

    from .i18n import t
    t("service.started", name="web", command="npm run dev")

Anahtar yoksa anahtarin kendisi doner: eksik ceviri gorunur olur ama
hicbir sey cokmez.
"""

from __future__ import annotations

from typing import Any

# Etkin dil. Surec basina tek deger: bir sunucu tek calisma alanina
# hizmet eder, o calisma alaninin da tek dili vardir.
_LANG = "tr"

SUPPORTED = ("tr", "en")


def set_language(lang: str | None) -> str:
    """Etkin dili ayarlar; taninmayan deger `tr` sayilir."""
    global _LANG
    _LANG = lang if lang in SUPPORTED else "tr"
    return _LANG


def language() -> str:
    return _LANG


def t(key: str, /, **values: Any) -> str:
    """Anahtarin etkin dildeki karsiligi.

    Bicimlendirme hatasi metni dusurmez: eksik bir degisken yuzunden
    kullaniciya hicbir sey gostermemektense ham sablonu gostermek iyidir.
    """
    entry = CATALOG.get(key)
    if entry is None:
        return key
    text = entry.get(_LANG) or entry.get("tr") or key
    if not values:
        return text
    try:
        return text.format(**values)
    except (KeyError, IndexError, ValueError):
        return text


# ---------------------------------------------------------------------- #
# Katalog
#
# Anahtarlar `alan.olay` bicimindedir. Iki dilin anahtar kumesi ayni
# olmali; `tests/test_i18n_py.py` bunu kilitler.
# ---------------------------------------------------------------------- #
CATALOG: dict[str, dict[str, str]] = {
    # ── Servisler ────────────────────────────────────────────────────── #
    "service.started": {
        "tr": "baslatildi: {name} · {command}",
        "en": "started: {name} · {command}",
    },
    "service.ready": {
        "tr": "hazir: {name} · http://127.0.0.1:{port}",
        "en": "ready: {name} · http://127.0.0.1:{port}",
    },
    "service.running": {
        "tr": "calisiyor: {name} (PID {pid})",
        "en": "running: {name} (PID {pid})",
    },
    "service.stopped": {
        "tr": "durduruldu: {name}",
        "en": "stopped: {name}",
    },
    "service.died": {
        "tr": "'{name}' kendiliginden sonlandi (cikis kodu {code})",
        "en": "'{name}' exited on its own (exit code {code})",
    },
    "service.already_running": {
        "tr": "'{name}' zaten calisiyor (PID {pid}{port}). Once durdurun ya da baska bir ad verin.",
        "en": "'{name}' is already running (PID {pid}{port}). Stop it first or use another name.",
    },
    "service.too_many": {
        "tr": "Ayni anda en fazla {limit} servis calisabilir. "
              "Kullanmadiginizi `stop_service` ile durdurun.",
        "en": "At most {limit} services can run at once. "
              "Stop the ones you no longer need with `stop_service`.",
    },
    "service.port_busy": {
        "tr": "{port} portu zaten kullaniliyor. Baska bir port secin ya da "
              "o portu tutan servisi durdurun.",
        "en": "Port {port} is already in use. Pick another port or stop the "
              "service holding it.",
    },
    "service.start_failed": {
        "tr": "Servis baslatilamadi: {error}",
        "en": "Could not start the service: {error}",
    },
    "service.exited_immediately": {
        "tr": "Servis hemen sonlandi (cikis kodu {code}).\n{log}",
        "en": "The service exited immediately (exit code {code}).\n{log}",
    },
    "service.not_listening": {
        "tr": "{port} portu {seconds} saniyede dinlemeye basmadi; surec (PID {pid}) "
              "hala calisiyor.\n{log}\nServis ayakta; `service_log` ile izleyin ya da "
              "`stop_service` ile durdurun.",
        "en": "Port {port} did not start listening within {seconds}s; the process "
              "(PID {pid}) is still running.\n{log}\nThe service is up; watch it with "
              "`service_log` or stop it with `stop_service`.",
    },
    "service.none": {
        "tr": "Calisan servis yok. Once `start_service` ile baslatin.",
        "en": "No service is running. Start one with `start_service` first.",
    },
    "service.unknown": {
        "tr": "'{name}' diye bir servis yok. Calisanlar: {running}",
        "en": "There is no service named '{name}'. Running: {running}",
    },
    "service.ambiguous": {
        "tr": "Birden fazla servis calisiyor; hangisi oldugunu yazin: {running}",
        "en": "Several services are running; say which one: {running}",
    },
    "service.no_manager": {
        "tr": "Servis yoneticisi bu baglamda yok.",
        "en": "No service manager in this context.",
    },
    "service.log_empty": {"tr": "(cikti yok)", "en": "(no output)"},
    "service.log_header": {
        "tr": "{name} · PID {pid} · {state}\n--- son {lines} satir ---\n",
        "en": "{name} · PID {pid} · {state}\n--- last {lines} lines ---\n",
    },
    "service.state_running": {"tr": "calisiyor", "en": "running"},
    "service.state_exited": {
        "tr": "SONLANDI (cikis kodu {code})",
        "en": "EXITED (exit code {code})",
    },
    "service.stopped_ok": {"tr": "{name} durduruldu.", "en": "{name} stopped."},
    "service.list_empty": {"tr": "Calisan servis yok.", "en": "No service is running."},
    "service.start_ok": {
        "tr": "{name} calisiyor (PID {pid}).",
        "en": "{name} is running (PID {pid}).",
    },
    "service.log_at": {"tr": "gunluk: {path}", "en": "log: {path}"},
    "service.address": {"tr": "adres: {url}", "en": "address: {url}"},
    "service.open_hint": {
        "tr": "Simdi `preview_open(port={port})` ile acip bakin.",
        "en": "Now open it with `preview_open(port={port})` and look.",
    },
    "service.first_lines": {
        "tr": "--- ilk satirlar ---\n{log}",
        "en": "--- first lines ---\n{log}",
    },
    "service.closed_all": {
        "tr": "servisler kapatildi: {names}",
        "en": "services shut down: {names}",
    },

    # ── Kabuk ─────────────────────────────────────────────────────────── #
    "shell.disabled": {
        "tr": "Kabuk erisimi kapali (deerx.toml -> [deerx.shell] enabled = false).",
        "en": "Shell access is off (deerx.toml -> [deerx.shell] enabled = false).",
    },
    "shell.empty": {"tr": "Bos komut.", "en": "Empty command."},
    "shell.denied_pattern": {
        "tr": "Komut politika geregi reddedildi (yasakli desen: '{pattern}').",
        "en": "Command refused by policy (forbidden pattern: '{pattern}').",
    },
    "shell.denied_command": {
        "tr": "Komut politika geregi reddedildi (yasakli komut: '{pattern}').",
        "en": "Command refused by policy (forbidden command: '{pattern}').",
    },
    "shell.not_allowed": {
        "tr": "Izin listesinde olmayan komut(lar): {names}. "
              "deerx.toml -> [deerx.shell] allow_prefixes listesine ekleyin.",
        "en": "Command(s) not in the allow list: {names}. "
              "Add them to deerx.toml -> [deerx.shell] allow_prefixes.",
    },
    "shell.not_a_dir": {"tr": "{path} bir dizin degil.", "en": "{path} is not a directory."},
    "shell.timeout": {
        "tr": "Komut {seconds}s icinde bitmedi; surec agaci sonlandirildi.{output}",
        "en": "The command did not finish within {seconds}s; the process tree was killed.{output}",
    },
    "shell.partial_output": {
        "tr": "\n--- o ana kadarki cikti ---\n{output}",
        "en": "\n--- output so far ---\n{output}",
    },
    "shell.start_failed": {
        "tr": "Komut baslatilamadi: {error}",
        "en": "Could not start the command: {error}",
    },
    "shell.no_multiline_shell": {
        "tr": "Bu makinede cok satirli komut calistirilamiyor: cmd.exe yeni satiri "
              "komut sonu sayar, ilk satiri calistirir ve gerisini sessizce atar.\n"
              "Komutu tek satira indirin ya da once `write_file` ile bir betik "
              "dosyasi yazip onu calistirin.",
        "en": "Multi-line commands cannot run on this machine: cmd.exe treats a "
              "newline as end-of-command, runs the first line and silently drops "
              "the rest.\nPut the command on one line, or write a script file with "
              "`write_file` first and run that.",
    },
    "shell.run": {"tr": "$ {command}", "en": "$ {command}"},
    "shell.approve": {
        "tr": "Komut calistir: {command}",
        "en": "Run command: {command}",
    },
    "shell.approve_detail": {
        "tr": "Dizin: {cwd}{chained}",
        "en": "Directory: {cwd}{chained}",
    },
    "shell.chained": {"tr": "\n(zincirlenmis komut)", "en": "\n(chained command)"},
    "shell.approve_service": {
        "tr": "Arka planda servis baslat: {command}",
        "en": "Start a background service: {command}",
    },
    "shell.approve_service_detail": {
        "tr": "Dizin: {cwd}{port}\n(kosu bitene kadar calisir)",
        "en": "Directory: {cwd}{port}\n(runs until the run ends)",
    },
    "shell.no_output": {"tr": "(cikti yok)", "en": "(no output)"},
    "sandbox.no_docker": {
        "tr": "Yalitilmis calistirma acik ama `docker` bulunamadi. Docker "
              "Desktop'i kurun ya da [deerx] execution = \"host\" yapin.",
        "en": "Isolated execution is on but `docker` was not found. Install "
              "Docker Desktop, or set [deerx] execution = \"host\".",
    },
    "images.searching": {"tr": "gorsel aramasi: {query}", "en": "image search: {query}"},
    "images.downloading": {"tr": "gorsel indiriliyor: {url}", "en": "downloading image: {url}"},
    "images.no_searxng": {
        "tr": "Gorsel aramasi SearXNG gerektirir. Ayarlar > Web arastirma "
              "bolumunden searxng_url tanimlayin (`deerx setup` kurabilir).",
        "en": "Image search needs SearXNG. Set searxng_url under Settings > "
              "Web research (`deerx setup` can install it).",
    },
    "images.search_failed": {"tr": "Gorsel aramasi basarisiz: {error}",
                             "en": "Image search failed: {error}"},
    "images.no_results": {"tr": "'{query}' icin gorsel bulunamadi.",
                          "en": "No images found for '{query}'."},
    "images.no_free_results": {
        "tr": "'{query}' icin lisansi bilinen kaynakta gorsel yok. Baska "
              "terimler deneyin; ya da free_only=false verin ve lisansi "
              "kendiniz dogrulayin -- teslimata girecekse atif sart.",
        "en": "No freely licensed image for '{query}'. Try other terms, or "
              "pass free_only=false and verify the licence yourself -- if it "
              "ships, attribution is required.",
    },
    "images.free": {"tr": "serbest lisans", "en": "free licence"},
    "images.unknown_licence": {"tr": "LISANS BELIRSIZ", "en": "LICENCE UNKNOWN"},
    "images.bad_name": {"tr": "Gecersiz dosya adi.", "en": "Invalid file name."},
    "images.approve": {"tr": "Gorsel indirilecek: {url}",
                       "en": "About to download an image: {url}"},
    "images.approve_detail": {"tr": "Kaydedilecek ad: {name}",
                              "en": "Will be saved as: {name}"},
    "images.download_failed": {"tr": "Gorsel indirilemedi: {error}",
                               "en": "Could not download the image: {error}"},
    "images.too_big": {"tr": "Gorsel {limit} MB sinirini asiyor.",
                       "en": "The image is larger than the {limit} MB limit."},
    "images.not_an_image": {
        "tr": "Gelen veri bir gorsel degil (icerik turu: {content_type}). "
              "Sunucu hata sayfasi donmus olabilir; adresi dogrulayin.",
        "en": "What came back is not an image (content type: {content_type}). "
              "The server may have returned an error page; check the address.",
    },
    "images.saved": {"tr": "{name} kaydedildi ({kb} KB). Kaynak: {url}",
                     "en": "{name} saved ({kb} KB). Source: {url}"},
    "images.artifact_summary": {"tr": "Gorsel ({kb} KB) — kaynak: {url}",
                                "en": "Image ({kb} KB) — source: {url}"},
    "sandbox.setup_running": {
        "tr": "Yalitilmis ortam hazirlaniyor (sandbox_setup)...",
        "en": "Preparing the isolated environment (sandbox_setup)...",
    },
    "sandbox.setup_failed": {
        "tr": "sandbox_setup basarisiz: {error}",
        "en": "sandbox_setup failed: {error}",
    },
    "sandbox.created": {
        "tr": "Yalitilmis ortam hazir: {name} ({image})",
        "en": "Isolated environment ready: {name} ({image})",
    },
    "sandbox.command_failed": {
        "tr": "docker {argv} basarisiz: {error}",
        "en": "docker {argv} failed: {error}",
    },
    "sandbox.port_outside_range": {
        "tr": "Port {port} yalitilmis ortamin disinda. Yayinlanan araliktan "
              "birini secin: {first}-{last}. Docker portlari konteyner "
              "kurulurken ayirir, sonradan eklenemez.",
        "en": "Port {port} is outside the isolated environment. Pick one from "
              "the published range: {first}-{last}. Docker reserves ports when "
              "the container is created; they cannot be added later.",
    },
    "sandbox.bind_all_interfaces": {
        "tr": "Yalitilmis ortamda servis 0.0.0.0 adresine baglanmali, "
              "127.0.0.1'e degil: yayinlanan port aksi halde bos kalir.",
        "en": "Inside the isolated environment a service must bind 0.0.0.0, "
              "not 127.0.0.1: otherwise the published port stays empty.",
    },
    "config.workspace_env_missing": {
        "tr": "DEERX_WORKSPACE bir dizini gostermiyor: {path}. Yok sayildi; "
              "bulunulan dizinden aranacak.",
        "en": "DEERX_WORKSPACE does not point at a directory: {path}. Ignored; "
              "falling back to the current directory.",
    },
    "config.missing_deerx_table": {
        "tr": "{path} icindeki ayarlar YOK SAYILDI: {keys}. Ayarlar [deerx] "
              "tablosunun altinda olmali. Dosyanin basina [deerx] satirini "
              "ekleyin.",
        "en": "Settings in {path} were IGNORED: {keys}. They must live under "
              "the [deerx] table. Add a [deerx] line at the top of the file.",
    },
    "web.google_missing": {
        "tr": "Google aramasi icin eksik ayar: {fields}. Ayarlar > Web "
              "arastirma bolumunden API anahtarini ve arama motoru kimligini "
              "(cx) girin; ikisi de gerekir.",
        "en": "Google search is missing settings: {fields}. Enter the API key "
              "and the search engine id (cx) under Settings > Web research; "
              "both are required.",
    },
    # 401/403 aldigimizda caresi bellidir; ciplak kodu gostermek kullaniciyi
    # kendi yapilandirmasinda aramaya gonderiyordu.
    "llm.screenshot_attached": {
        "tr": "Yukaridaki aracin aldigi ekran goruntusu. Ne GORDUGUNE bak: "
              "hizalama, tasma, okunurluk, kirpilmis gorsel.",
        "en": "The screenshot the tool above captured. Look at what you SEE: "
              "alignment, overflow, readability, cropped images.",
    },
    "llm.screenshot_dropped": {
        "tr": "(gorsel cikarildi: uc goruntu kabul etmiyor)",
        "en": "(image removed: the endpoint does not accept images)",
    },
    "llm.vision_unsupported": {
        "tr": "uc goruntu kabul etmedi; bu kosuda ekran goruntuleri metin "
              "olarak bildirilecek",
        "en": "the endpoint rejected images; screenshots will be reported as "
              "text for this run",
    },
    "llm.auth_needs_key": {
        "tr": " — uc kimlik dogrulama istiyor ama yapilandirilmis bir anahtar "
              "yok. Calisma alanindaki .env dosyasina OPENAI_API_KEY ekleyin "
              "ya da Ayarlar ekranindan girin.",
        "en": " — the endpoint requires authentication but no key is "
              "configured. Add OPENAI_API_KEY to the workspace .env file, or "
              "enter it on the Settings screen.",
    },
    "llm.auth_key_rejected": {
        "tr": " — uc yapilandirilmis anahtari reddetti; anahtarin bu uc icin "
              "dogru oldugunu dogrulayin.",
        "en": " — the endpoint rejected the configured key; check that the key "
              "is the right one for this endpoint.",
    },
    # Cikis kodu notlari. Harness bu sayilarin ne demek oldugunu biliyor;
    # modele ciplak sayi gostermek onu kendi kodunda olmayan bir hatayi
    # aramaya gonderiyor.
    "shell.exit_ctrl_c": {
        "tr": "Ctrl+C / Ctrl+Break konsol olayiyla sonlandirildi",
        "en": "terminated by a Ctrl+C / Ctrl+Break console event",
    },
    "shell.exit_access_violation": {
        "tr": "erisim ihlali (bellek hatasi)",
        "en": "access violation (memory fault)",
    },
    "shell.exit_stack_overflow": {
        "tr": "yigin tasmasi",
        "en": "stack overflow",
    },
    "shell.exit_buffer_overrun": {
        "tr": "yigin tampon tasmasi tespit edildi",
        "en": "stack buffer overrun detected",
    },
    "shell.exit_dll_init": {
        "tr": "DLL baslatma hatasi (eksik ya da uyumsuz bagimlilik)",
        "en": "DLL initialisation failed (missing or mismatched dependency)",
    },
    "shell.exit_signal": {
        "tr": "{signal} sinyaliyle sonlandirildi",
        "en": "terminated by signal {signal}",
    },

    # -- Faz etiketleri --------------------------------------------------- #
    #
    # Ayni anahtarlar `static/i18n.js` icinde de var: arayuz faz adini
    # istemci tarafinda cozer, CLI burada. `tests/test_i18n_py.py` ikisinin
    # ayni fazlari kapsadigini kilitler.

    "phase.ingest": {
        "tr": "Doküman alımı",
        "en": "Ingest",
    },
    "phase.analyze": {
        "tr": "Analiz",
        "en": "Analysis",
    },
    "phase.research": {
        "tr": "Araştırma",
        "en": "Research",
    },
    "phase.assess": {
        "tr": "Boşluk ve risk",
        "en": "Gaps and risks",
    },
    "phase.mockup": {
        "tr": "Mockup",
        "en": "Mockups",
    },
    "phase.design": {
        "tr": "Mimari",
        "en": "Architecture",
    },
    "phase.plan": {
        "tr": "Plan",
        "en": "Plan",
    },
    "phase.implement": {
        "tr": "Uygulama",
        "en": "Implementation",
    },
    "phase.qa": {
        "tr": "QA",
        "en": "QA",
    },
    "phase.review": {
        "tr": "Kod incelemesi",
        "en": "Code review",
    },
    "phase.package": {
        "tr": "Teslimat",
        "en": "Delivery",
    },
    "phase.staging": {
        "tr": "Staging",
        "en": "Staging",
    },
    "phase.live": {
        "tr": "Canlı",
        "en": "Live",
    },

    "agent.ingest": {
        "tr": "—",
        "en": "—",
    },
    "agent.analyze": {
        "tr": "Analist",
        "en": "Analyst",
    },
    "agent.research": {
        "tr": "Araştırmacı",
        "en": "Researcher",
    },
    "agent.assess": {
        "tr": "Değerlendirici",
        "en": "Assessor",
    },
    "agent.mockup": {
        "tr": "Mockup",
        "en": "Mockup",
    },
    "agent.design": {
        "tr": "Mimar",
        "en": "Architect",
    },
    "agent.plan": {
        "tr": "Planlayıcı",
        "en": "Planner",
    },
    "agent.implement": {
        "tr": "Arka uç / Ön yüz / QA",
        "en": "Backend / Frontend / QA",
    },
    "agent.qa": {
        "tr": "QA",
        "en": "QA",
    },
    "agent.review": {
        "tr": "İnceleyici",
        "en": "Reviewer",
    },
    "agent.package": {
        "tr": "—",
        "en": "—",
    },
    "agent.staging": {
        "tr": "Staging",
        "en": "Staging",
    },
    "agent.live": {
        "tr": "Canlı",
        "en": "Live",
    },

    "produces.ingest": {
        "tr": "Şartname ve mevcut kod → aranabilir bilgi tabanı",
        "en": "Spec and existing code → searchable knowledge base",
    },
    "produces.analyze": {
        "tr": "Gereksinimler, belirsizlikler, size sorulacak sorular",
        "en": "Requirements, ambiguities, questions for you",
    },
    "produces.research": {
        "tr": "Web'de sürüm ve standart doğrulaması, kaynaklı notlar",
        "en": "Version and standard checks on the web, with sources",
    },
    "produces.assess": {
        "tr": "Şartname ile kod arasındaki boşluklar ve riskler",
        "en": "Gaps and risks between the spec and the code",
    },
    "produces.mockup": {
        "tr": "Çalışan tek dosyalık HTML ekranlar",
        "en": "Working single-file HTML screens",
    },
    "produces.design": {
        "tr": "Mimari kararlar (ADR) ve veri modeli",
        "en": "Architecture decisions (ADRs) and data model",
    },
    "produces.plan": {
        "tr": "Şeritlere bölünmüş, bağımlılıklı görev listesi",
        "en": "Task list split into lanes, with dependencies",
    },
    "produces.implement": {
        "tr": "Kod — her görev kendi şeridinin uzmanına gider",
        "en": "Code — each task goes to its lane's specialist",
    },
    "produces.qa": {
        "tr": "Test yazımı ve koşumu, kenar durum taraması",
        "en": "Writing and running tests, edge-case sweep",
    },
    "produces.review": {
        "tr": "Gereksinim izlemesi ve kod denetimi",
        "en": "Requirement tracing and code audit",
    },
    "produces.package": {
        "tr": "Hazırlık kapısı + teslim edilebilir zip",
        "en": "Readiness gate + deliverable zip",
    },
    "produces.staging": {
        "tr": "Temiz ortamda kurulum ve duman testi",
        "en": "Clean-room install and smoke test",
    },
    "produces.live": {
        "tr": "Çıkış kapısı, dağıtım, geri alma planı",
        "en": "Release gate, deployment, rollback plan",
    },

    # ── Fazlar ────────────────────────────────────────────────────────── #
    "phase.no_deliverable": {
        "tr": "faz ciktisi uretilmedi ({pattern}); ajan bir kez daha deneniyor",
        "en": "the phase produced no deliverable ({pattern}); retrying the agent once",
    },
    "phase.missing_deliverable": {
        "tr": "Faz ciktisi uretilmedi: `{pattern}` ({what}). Ajan {turns} turda durdu. "
              "Sonraki fazlar bu ciktiya dayandigi icin boru hatti burada durdu.",
        "en": "The phase produced no deliverable: `{pattern}` ({what}). The agent "
              "stopped after {turns} turns. Later phases depend on it, so the "
              "pipeline stopped here.",
    },
    "phase.nudge": {
        "tr": "Bu fazi ciktisini uretmeden bitirdin. Beklenen: `{pattern}` ({what}).\n\n"
              "Okumak ve arastirmak isin yarisi; kalan yarisi ureten kisim. "
              "Simdi `save_artifact` ile o ciktiyi yaz ve fazin kayit araclarini "
              "(gereksinim / bosluk / karar / gorev) kullan. Yeniden ozet gecme, "
              "dogrudan uret.",
        "en": "You ended this phase without producing its deliverable. Expected: "
              "`{pattern}` ({what}).\n\nReading and researching is half the job; the "
              "other half is producing. Write that deliverable now with "
              "`save_artifact` and use the phase's recording tools (requirements / "
              "gaps / decisions / tasks). Do not summarise again -- produce.",
    },
    "phase.skipped": {"tr": "atlandi ({reason})", "en": "skipped ({reason})"},
    "phase.failed": {"tr": "faz basarisiz", "en": "phase failed"},
    "run.cancelled": {
        "tr": "kosu durduruldu; kalan fazlar atlandi",
        "en": "run cancelled; remaining phases skipped",
    },

    # ── Ajan dongusu ──────────────────────────────────────────────────── #
    "agent.started": {"tr": "basladi", "en": "started"},
    "agent.cancelled": {"tr": "kosu durduruldu", "en": "run cancelled"},
    "agent.refusal": {
        "tr": "Model istegi guvenlik gerekcesiyle reddetti.",
        "en": "The model refused the request on safety grounds.",
    },
    "agent.server_tool_paused": {
        "tr": "sunucu araci duraklatti, devam ediliyor",
        "en": "server tool paused; continuing",
    },
    "agent.max_iterations": {
        "tr": "iterasyon siniri ({limit}) doldu; sonuc eksik olabilir",
        "en": "iteration limit ({limit}) reached; the result may be incomplete",
    },
    "agent.truncated": {
        "tr": "yanit uretim tavaninda kesildi ({n}. kez); kaldigi yerden devam etmesi istendi",
        "en": "the answer was cut off at the output ceiling ({n}); asked to continue",
    },
    "agent.truncated_giving_up": {
        "tr": "Model yanitini {n} kez uretim tavaninda kesti. `max_tokens` degerini "
              "yukseltin ya da gorevi kucultun.",
        "en": "The model hit the output ceiling {n} times. Raise `max_tokens` or "
              "make the task smaller.",
    },
    "agent.budget_warning": {
        "tr": "tur butcesinin sonuna yaklasildi ({used}/{total})",
        "en": "approaching the turn budget ({used}/{total})",
    },
    "agent.thinking_overrun": {
        "tr": "Uretim tavanina takildin ve butun butceyi DUSUNMEYE harcadin: "
              "ne bir cevap ne bir arac cagrisi cikti.\n\n"
              "Daha az dusun, daha erken davran. Bir sonraki turda once bir "
              "ARAC CAGIR -- elindeki en kucuk adimi at, sonucu gor, sonra "
              "devam et. Butun isi tek turda planlamaya calisma; plani "
              "yaparken butce bitiyor.",
        "en": "You hit the output ceiling and spent the entire budget "
              "THINKING: neither an answer nor a tool call came out.\n\n"
              "Think less, act sooner. On the next turn CALL A TOOL first -- "
              "take the smallest step available, see the result, then "
              "continue. Do not try to plan the whole job in one turn; the "
              "budget runs out while you are planning.",
    },
    "agent.thinking_overrun_giving_up": {
        "tr": "Model {n} kez butun uretim butcesini dusunmeye harcadi ve "
              "cevaba ulasmadi. `max_tokens` degerini yukseltin (akil yurutme "
              "modellerinde bu deger dusunmeyi DE kapsar) ya da fazi kucultun.",
        "en": "The model spent its entire output budget thinking {n} times "
              "without reaching an answer. Raise `max_tokens` (on a reasoning "
              "model it covers the thinking too) or make the phase smaller.",
    },
    "agent.truncated_hint": {
        "tr": "Onceki mesajin, tur basina uretim tavanina takildigi icin YARIDA "
              "KESILDI; yazmakta oldugun sey kaydedilmedi.\n\n"
              "Bastan ozetleme, kaldigin yerden tamamla. Uzun bir cikti yaziyorsan "
              "onu daha kisa tut ya da parca parca yaz -- once `save_artifact` ile "
              "ozu kaydet, ayrintiyi sonra ekle. Ayni uzunlukta tekrar denemek ayni "
              "yerde kesilir.",
        "en": "Your previous message was CUT OFF at the per-turn output ceiling; "
              "what you were writing was not saved.\n\n"
              "Do not start over -- continue from where you stopped. If you are "
              "writing a long deliverable, keep it shorter or write it in pieces: "
              "save the essentials with `save_artifact` first and add detail after. "
              "Retrying at the same length will be cut at the same place.",
    },
    "agent.budget_hint": {
        "tr": "TUR BUTCESI: {left} tur kaldi (toplam {total}). Butce dolunca "
              "durdurulursun ve o ana kadar KAYDETMEDIGIN her sey kaybolur.\n\n"
              "Simdi toparla: once fazin asil ciktisini `save_artifact` ile yaz ve "
              "kayit araclarini (gereksinim / bosluk / karar / gorev) calistir. "
              "Kalan arastirmayi sonra yaparsin; kaydedilmemis bir inceleme hic "
              "yapilmamis sayilir.",
        "en": "TURN BUDGET: {left} turns left (of {total}). When it runs out you are "
              "stopped, and anything you have NOT SAVED by then is lost.\n\n"
              "Wrap up now: write the phase's deliverable with `save_artifact` first "
              "and run the recording tools (requirements / gaps / decisions / tasks). "
              "Do the remaining research after; an unsaved review counts as never "
              "having happened.",
    },


    # ── CLI: uygulama ve komut yardimlari ─────────────────────────────── #
    #
    # Yardim metinleri Typer dekoratorlerinde, yani ICE AKTARMA aninda
    # hesaplanir; `Settings` yuklenmesini bekleyemezler. `cli._early_language`
    # dili once belirler, bu anahtarlar da o dille cozulur.
    "cli.app": {
        "tr": "Dokuman-gudumlu proje gelistirme ajani: "
              "analiz -> arastirma -> tasarim -> plan -> uygulama.",
        "en": "Document-driven project development agent: "
              "analysis -> research -> design -> plan -> build.",
    },
    "cli.init": {
        "tr": "Yeni bir DeerX calisma alani kurar (deerx.toml + dizinler).",
        "en": "Sets up a new DeerX workspace (deerx.toml + directories).",
    },
    "cli.ingest": {
        "tr": "Dokumanlari ve kodu bilgi tabanina indeksler.",
        "en": "Indexes documents and code into the knowledge base.",
    },
    "cli.search": {
        "tr": "Bilgi tabaninda hibrit arama yapar.",
        "en": "Runs a hybrid search over the knowledge base.",
    },
    "cli.run": {
        "tr": "Boru hattini calistirir (faz araligi verilebilir).",
        "en": "Runs the pipeline (a phase range can be given).",
    },
    "cli.phase": {"tr": "Tek bir fazi calistirir.", "en": "Runs a single phase."},
    "cli.implement": {
        "tr": "Plandaki gorevleri uygular.",
        "en": "Implements the tasks in the plan.",
    },
    "cli.status": {"tr": "Proje durumunu gosterir.", "en": "Shows the project status."},
    "cli.tasks": {
        "tr": "Gelistirme gorevlerini listeler.",
        "en": "Lists the development tasks.",
    },
    "cli.package": {
        "tr": "Hazirlik kapisini yoklar ve teslimat zip'i uretir.",
        "en": "Checks the readiness gate and produces the delivery zip.",
    },
    "cli.questions": {
        "tr": "Ajanlarin size sordugu acik sorulari listeler.",
        "en": "Lists the open questions the agents asked you.",
    },
    "cli.answer": {
        "tr": "Bir soruyu cevaplar; cevap bilgi tabanina da yazilir.",
        "en": "Answers a question; the answer is also written to the knowledge base.",
    },
    "cli.skip": {
        "tr": "Soruyu atlar; ajanlar belirtilen varsayimla ilerler.",
        "en": "Skips the question; the agents proceed with the stated assumption.",
    },
    "cli.artifacts": {
        "tr": "Uretilen ciktilari listeler veya birini goruntuler.",
        "en": "Lists the produced artifacts, or shows one of them.",
    },
    "cli.serve": {"tr": "Web arayuzunu baslatir.", "en": "Starts the web interface."},
    "cli.mcp": {
        "tr": "MCP sunucusunu stdio uzerinde calistirir.",
        "en": "Runs the MCP server over stdio.",
    },
    "cli.doctor": {
        "tr": "Ortami kontrol eder: anahtar, bagimliliklar, bilgi tabani.",
        "en": "Checks the environment: keys, dependencies, knowledge base.",
    },
    "cli.user": {
        "tr": "Kullanici hesaplari (web arayuzu icin).",
        "en": "User accounts (for the web interface).",
    },
    "cli.user_add": {
        "tr": "Yeni kullanici olusturur. Parola sorulur, argumanla alinmaz.",
        "en": "Creates a new user. The password is prompted, never taken as an argument.",
    },
    "cli.user_list": {"tr": "Kullanicilari listeler.", "en": "Lists the users."},
    "cli.user_passwd": {
        "tr": "Parolayi degistirir. Acik oturumlarin hepsi duser.",
        "en": "Changes the password. All open sessions are dropped.",
    },
    "cli.user_ensure": {
        "tr": "Hesabi olusturur ya da parolasini sifirlar; hangisi gerekiyorsa.",
        "en": "Creates the account or resets its password, whichever is needed.",
    },
    "cli.user_disable": {
        "tr": "Hesabi kapatir. Silmez; acik oturumlari dusurur, giris engellenir.",
        "en": "Disables the account. Does not delete it; drops open sessions and blocks sign-in.",
    },
    "cli.user_enable": {
        "tr": "Kapatilmis hesabi yeniden acar.",
        "en": "Re-enables a disabled account.",
    },
    "cli.user_delete": {
        "tr": "Kullaniciyi siler. Ana yonetici silinemez.",
        "en": "Deletes the user. The primary administrator cannot be deleted.",
    },

    # ── CLI: secenek yardimlari ───────────────────────────────────────── #
    "opt.project_dir": {"tr": "Proje dizini.", "en": "Project directory."},
    "opt.force_overwrite": {"tr": "Var olan dosyalari ez.", "en": "Overwrite existing files."},
    "opt.paths": {"tr": "Dosya/dizin yollari.", "en": "File/directory paths."},
    "opt.reindex": {
        "tr": "Degismemis dosyalari da yeniden isle.",
        "en": "Reprocess unchanged files as well.",
    },
    "opt.query": {"tr": "Arama sorgusu.", "en": "Search query."},
    "opt.count": {"tr": "Sonuc sayisi.", "en": "Number of results."},
    "opt.kinds": {"tr": "doc | code | web | data", "en": "doc | code | web | data"},
    "opt.full_chunks": {
        "tr": "Parcalarin tamamini goster.",
        "en": "Show the full chunks.",
    },
    "opt.from_phase": {"tr": "Baslangic fazi.", "en": "Starting phase."},
    "opt.to_phase": {"tr": "Bitis fazi.", "en": "Ending phase."},
    "opt.source": {
        "tr": "Indekslenecek sartname dosyasi/dizini.",
        "en": "Specification file/directory to index.",
    },
    "opt.goal": {
        "tr": "Kullanici hedefi (ajanlara baglam olarak gecer).",
        "en": "User goal (passed to the agents as context).",
    },
    "opt.brief": {
        "tr": "Ajanlara verilecek serbest talimat; @dosya.md ile dosyadan okunur.",
        "en": "Free-form instruction for the agents; @file.md reads it from a file.",
    },
    "opt.force_phases": {
        "tr": "Tamamlanmis fazlari da tekrar calistir.",
        "en": "Re-run completed phases as well.",
    },
    "opt.yes_auto": {
        "tr": "Onay sormadan calistir (approval_mode=auto).",
        "en": "Run without asking for approval (approval_mode=auto).",
    },
    "opt.dry_run": {
        "tr": "Yazma islemlerini uygulama.",
        "en": "Do not apply write operations.",
    },
    "opt.phase_name": {"tr": "Faz adi.", "en": "Phase name."},
    "opt.task_only": {
        "tr": "Yalnizca bu gorevi uygula, or. T-003.",
        "en": "Implement only this task, e.g. T-003.",
    },
    "opt.yes": {"tr": "Onay sormadan calistir.", "en": "Run without asking for approval."},
    "opt.task_status": {
        "tr": "pending | running | done | blocked | failed",
        "en": "pending | running | done | blocked | failed",
    },
    "opt.username_lower": {"tr": "Kullanici adi (kucuk harf).", "en": "Username (lower case)."},
    "opt.admin": {"tr": "Yonetici yetkisi ver.", "en": "Grant administrator rights."},
    "opt.display_name": {"tr": "Gorunen ad.", "en": "Display name."},
    "opt.username": {"tr": "Kullanici adi.", "en": "Username."},
    "opt.password_stdin": {
        "tr": "Parolayi standart girdiden oku (betikler icin; sorulmaz).",
        "en": "Read the password from standard input (for scripts; no prompt).",
    },
    "opt.no_confirm": {"tr": "Onay sorma.", "en": "Do not ask for confirmation."},
    "opt.package_force": {
        "tr": "Hazirlik denetimini gecerek yine de paketle.",
        "en": "Package anyway, bypassing the readiness check.",
    },
    "opt.out_dir": {
        "tr": "Zip dosyasinin yazilacagi dizin.",
        "en": "Directory to write the zip file into.",
    },
    "opt.show_answered": {
        "tr": "Cevaplanmislari da goster.",
        "en": "Show answered ones as well.",
    },
    "opt.question_key": {"tr": "Soru anahtari, or. Q-001.", "en": "Question key, e.g. Q-001."},
    "opt.answer_text": {"tr": "Cevabiniz.", "en": "Your answer."},
    "opt.answer_file": {"tr": "Cevabi dosyadan oku.", "en": "Read the answer from a file."},
    "opt.assumption": {
        "tr": "Ajanlarin ilerleyecegi varsayim.",
        "en": "The assumption the agents will proceed with.",
    },
    "opt.artifact_name": {
        "tr": "Goruntulenecek cikti adi.",
        "en": "Name of the artifact to show.",
    },
    "opt.host": {"tr": "Dinlenecek adres.", "en": "Address to listen on."},
    "opt.port": {"tr": "Port.", "en": "Port."},
    "opt.workspace": {"tr": "Calisma alani.", "en": "Workspace."},
    "opt.open_browser": {"tr": "Tarayiciyi ac.", "en": "Open the browser."},
    "opt.mcp_workspace": {
        "tr": "MCP sunucusunun calisma alani.",
        "en": "Workspace for the MCP server.",
    },

    # ── CLI: calisma ani ciktilari ────────────────────────────────────── #
    "cli.empty_answer": {
        "tr": "Bos cevap. Varsayimla gecmek icin `deerx skip` kullanin.",
        "en": "Empty answer. Use `deerx skip` to move on with an assumption.",
    },
    "cli.no_such_question": {
        "tr": "'{key}' diye bir soru yok.",
        "en": "There is no question named '{key}'.",
    },
    "cli.no_questions": {
        "tr": "Hic soru kaydedilmemis.",
        "en": "No questions have been recorded.",
    },
    "cli.no_open_questions": {
        "tr": "Cevap bekleyen soru yok.",
        "en": "No questions are waiting for an answer.",
    },
    "cli.file_missing": {"tr": "Dosya diskte yok: {path}", "en": "File not on disk: {path}"},
    "cli.skipped": {
        "tr": "{key} atlandi -- varsayim: {assumption}",
        "en": "{key} skipped -- assumption: {assumption}",
    },
    "cli.own_assumption": {
        "tr": "(ajan kendi varsayimini kuracak)",
        "en": "(the agent will form its own assumption)",
    },
    "cli.no_pending_left": {
        "tr": "Bekleyen soru kalmadi.",
        "en": "No pending questions left.",
    },
    "cli.continue_hint": {
        "tr": "Devam edin:  [bold]deerx run --from <faz>[/bold]",
        "en": "Continue with:  [bold]deerx run --from <phase>[/bold]",
    },
    "cli.workspace_ready": {
        "tr": "[ok]Calisma alani hazir:[/ok] {path}",
        "en": "[ok]Workspace ready:[/ok] {path}",
    },
    # OLCULDU (kabul testi): burada "ANTHROPIC_API_KEY yazin" yaziyordu,
    # oysa `init`in YAZDIGI yapilandirma `provider = "openai"` ve yerel bir
    # uca bakiyor -- cogu yerel sunucu anahtar bile istemez. Yeni
    # kullanicinin okudugu ILK yonerge onu yanlis saglayiciya gonderiyordu.
    "cli.step_key": {
        "tr": "  1. Modeli baglayin: [bold]deerx.toml[/bold] icinde "
              "openai_base_url ve model adlari (anahtar gerekiyorsa "
              "[bold].env[/bold]), sonra [bold]deerx doctor[/bold]",
        "en": "  1. Connect your model: openai_base_url and the model names "
              "in [bold]deerx.toml[/bold] (a key, if one is needed, goes in "
              "[bold].env[/bold]), then [bold]deerx doctor[/bold]",
    },
    # `.env` dosyasinin ICERIGI. Tek bir saglayicinin anahtarini tohum
    # olarak yazmak, o saglayiciyi kullanmayan herkese yanlis ipucu verir.
    "cli.env_template": {
        "tr": (
            "# Anahtarlar burada durur; deerx.toml'a YAZILMAZ.\n"
            "# Yerel bir OpenAI-uyumlu uc (vLLM, Ollama, LM Studio) cogu\n"
            "# zaman anahtar istemez -- ikisi de bos kalabilir.\n"
            "OPENAI_API_KEY=\n"
            "ANTHROPIC_API_KEY=\n"
        ),
        "en": (
            "# Keys live here; never in deerx.toml.\n"
            "# A local OpenAI-compatible endpoint (vLLM, Ollama, LM Studio)\n"
            "# usually needs no key -- both may stay empty.\n"
            "OPENAI_API_KEY=\n"
            "ANTHROPIC_API_KEY=\n"
        ),
    },
    "cli.step_spec": {
        "tr": "  2. Sartnamenizi [bold]{path}[/bold] altina koyun",
        "en": "  2. Put your specification under [bold]{path}[/bold]",
    },
    "cli.step_run": {
        "tr": "  3. [bold]deerx run[/bold] ile boru hattini baslatin",
        "en": "  3. Start the pipeline with [bold]deerx run[/bold]",
    },
    "cli.workspace": {"tr": "Calisma alani", "en": "Workspace"},
    "cli.next_steps": {"tr": "Sonraki adimlar:", "en": "Next steps:"},
    "cli.settings_at": {"tr": "Ayarlar: {path}", "en": "Settings: {path}"},
    "cli.password_again": {"tr": "Parola (tekrar): ", "en": "Password (again): "},
    "cli.answer_or_assumption": {"tr": "Cevap / varsayim", "en": "Answer / assumption"},

    # -- CLI: tablo basliklari ------------------------------------------ #
    "col.document": {"tr": "Dokuman", "en": "Document"},
    "col.kind": {"tr": "Tur", "en": "Kind"},
    "col.chunk": {"tr": "Parca", "en": "Chunk"},
    "col.phase": {"tr": "Faz", "en": "Phase"},
    "col.status": {"tr": "Durum", "en": "Status"},
    "col.cost": {"tr": "Maliyet", "en": "Cost"},
    "col.summary": {"tr": "Ozet", "en": "Summary"},
    "col.note": {"tr": "Not", "en": "Note"},
    "col.key": {"tr": "Anahtar", "en": "Key"},
    "col.title": {"tr": "Baslik", "en": "Title"},
    "col.deps": {"tr": "Bagimlilik", "en": "Depends on"},
    "col.blocking": {"tr": "Bloke", "en": "Blocks"},
    "col.question": {"tr": "Soru", "en": "Question"},
    "col.name": {"tr": "Ad", "en": "Name"},
    "col.username": {"tr": "Kullanici", "en": "User"},
    "col.role": {"tr": "Rol", "en": "Role"},
    "col.last_login": {"tr": "Son giris", "en": "Last sign-in"},
    "col.check": {"tr": "Kontrol", "en": "Check"},
    "col.detail": {"tr": "Detay", "en": "Detail"},

    # -- CLI: uzun yardim metinleri -------------------------------------- #
    "cli.run_detail": {
        "tr": "Varsayilan aralik [bold]ingest -> plan[/bold]: analiz, arastirma, "
              "bosluk degerlendirmesi, mockup, mimari ve gelistirme plani "
              "uretilir; kod yazilmaz. Kodu da yazdirmak icin [bold]--to review"
              "[/bold], dagitima kadar gitmek icin [bold]--to live[/bold] verin."
              "\n\nBir ajan yalnizca sizin cevaplayabileceginiz bir soru "
              "kaydederse boru hatti orada durur ve sorulari gosterir. "
              "[bold]deerx answer[/bold] ile cevaplayip kaldiginiz yerden devam "
              "edersiniz.",
        "en": "The default range is [bold]ingest -> plan[/bold]: analysis, "
              "research, gap assessment, mockup, architecture and the "
              "development plan are produced; no code is written. Add "
              "[bold]--to review[/bold] to have the code written too, or "
              "[bold]--to live[/bold] to go all the way to deployment."
              "\n\nIf an agent records a question only you can answer, the "
              "pipeline stops there and shows the questions. Answer it with "
              "[bold]deerx answer[/bold] and continue from where you left off.",
    },
    "cli.package_detail": {
        "tr": "Once hazirlik denetimi yapilir: tamamlanmamis veya basarisiz "
              "gorev, cevaplanmamis bloke edici soru varsa paketleme durur. "
              "Sirlar ([bold].env[/bold], anahtar dosyalari) pakete ASLA girmez.",
        "en": "A readiness check runs first: packaging stops if there is an "
              "unfinished or failed task, or an unanswered blocking question. "
              "Secrets ([bold].env[/bold], key files) NEVER enter the package.",
    },
    "cli.serve_detail": {
        "tr": "Panodan boru hattini calistirabilir, canli olay akisini "
              "izleyebilir, onay isteklerini cevaplayabilir ve uretilen "
              "ciktilari goruntuleyebilirsiniz.",
        "en": "From the dashboard you can run the pipeline, watch the live event "
              "stream, answer approval requests and view the produced artifacts.",
    },

    # -- CLI: calisma ani ciktilari (devam) ------------------------------ #
    "cli.config_error": {"tr": "Konfigurasyon hatasi:", "en": "Configuration error:"},
    "cli.already_exists": {
        "tr": "{path} zaten var. Uzerine yazmak icin --force kullanin.",
        "en": "{path} already exists. Use --force to overwrite it.",
    },
    "cli.index_failed": {"tr": "indeksleme basarisiz", "en": "indexing failed"},
    "cli.kb": {"tr": "Bilgi tabani", "en": "Knowledge base"},
    "cli.no_results": {
        "tr": "[warn]Sonuc yok.[/warn] Bilgi tabani: {stats}",
        "en": "[warn]No results.[/warn] Knowledge base: {stats}",
    },
    "cli.score": {"tr": "skor {score} · {kind}", "en": "score {score} · {kind}"},
    "cli.approval_mode": {"tr": "Onay modu", "en": "Approval mode"},
    "cli.models": {"tr": "Modeller", "en": "Models"},
    "cli.phases_to_run": {"tr": "Calistirilacak fazlar", "en": "Phases to run"},
    "cli.no_summary": {"tr": "(ozet yok)", "en": "(no summary)"},
    "cli.implementation": {"tr": "Uygulama", "en": "Implementation"},
    "cli.phases": {"tr": "Fazlar", "en": "Phases"},
    "cli.goal": {"tr": "Hedef", "en": "Goal"},
    "cli.kb_stats": {
        "tr": "{documents} dokuman · {chunks} parca · {model}",
        "en": "{documents} documents · {chunks} chunks · {model}",
    },
    "cli.records": {"tr": "Kayitlar", "en": "Records"},
    "cli.record_stats": {
        "tr": "{requirements} gereksinim · {gaps} bosluk · {decisions} karar "
              "· {notes} bulgu",
        "en": "{requirements} requirements · {gaps} gaps · {decisions} "
              "decisions · {notes} findings",
    },
    "cli.questions_label": {"tr": "Sorular", "en": "Questions"},
    "cli.question_stats": {
        "tr": "{open} acik / {total} toplam",
        "en": "{open} open / {total} total",
    },
    "cli.blocking_suffix": {
        "tr": " · {count} tanesi boru hattini durduruyor",
        "en": " · {count} of them stop the pipeline",
    },
    "cli.tasks_label": {"tr": "Gorevler", "en": "Tasks"},
    "cli.task_stats": {"tr": "{done}/{total} tamamlandi", "en": "{done}/{total} done"},
    "cli.artifacts_label": {"tr": "Ciktilar", "en": "Artifacts"},
    "cli.no_tasks": {
        "tr": "[warn]Gorev yok.[/warn] Once `deerx phase plan` calistirin.",
        "en": "[warn]No tasks.[/warn] Run `deerx phase plan` first.",
    },
    "cli.ready_legend": {
        "tr": "{glyph} = bagimliliklari tamam, uygulanmaya hazir",
        "en": "{glyph} = dependencies met, ready to implement",
    },
    "cli.password": {"tr": "Parola: ", "en": "Password: "},
    "cli.password_stdin_empty": {
        "tr": "Standart girdiden parola gelmedi.",
        "en": "No password arrived on standard input.",
    },
    "cli.password_mismatch": {
        "tr": "Parolalar eslesmedi.",
        "en": "The passwords did not match.",
    },
    "cli.first_account": {
        "tr": "Ilk hesap: ana yonetici olarak olusturuldu.",
        "en": "First account: created as the primary administrator.",
    },
    "cli.user_created": {
        "tr": "{name} olusturuldu ({role}).",
        "en": "{name} created ({role}).",
    },
    "cli.no_users": {
        "tr": "[warn]Kullanici yok — kimlik dogrulama kapali.[/warn]\n"
              "[dim]Olusturmak icin:[/dim] deerx user add <ad> --admin",
        "en": "[warn]No users — authentication is off.[/warn]\n"
              "[dim]To create one:[/dim] deerx user add <name> --admin",
    },
    "cli.users_title": {
        "tr": "Kullanicilar · {workspace}",
        "en": "Users · {workspace}",
    },
    "cli.master_suffix": {"tr": " (ana)", "en": " (primary)"},
    "cli.active": {"tr": "acik", "en": "active"},
    "cli.inactive": {"tr": "KAPALI", "en": "DISABLED"},
    "cli.no_such_user": {
        "tr": "'{name}' diye bir kullanici yok.",
        "en": "There is no user named '{name}'.",
    },
    "cli.password_changed": {
        "tr": "{name} parolasi degisti; oturumlari kapandi.",
        "en": "The password for {name} changed; their sessions were closed.",
    },
    "cli.user_enabled": {"tr": "{name} acildi.", "en": "{name} was enabled."},
    "cli.user_disabled": {
        "tr": "{name} kapatildi; oturumlari dusuruldu.",
        "en": "{name} was disabled; their sessions were dropped.",
    },
    "cli.confirm_delete": {"tr": "{name} silinsin mi?", "en": "Delete {name}?"},
    "cli.user_deleted": {"tr": "{name} silindi.", "en": "{name} was deleted."},
    "cli.package_force_hint": {
        "tr": "[dim]Yine de paketlemek icin:[/dim] [bold]deerx package --force[/bold]",
        "en": "[dim]To package anyway:[/dim] [bold]deerx package --force[/bold]",
    },
    "cli.not_ready": {
        "tr": "Proje teslime hazir degil",
        "en": "The project is not ready for delivery",
    },
    "cli.package_files": {
        "tr": "{count} dosya · {megabytes} MB",
        "en": "{count} files · {megabytes} MB",
    },
    "cli.secrets_excluded": {
        "tr": "[warn]{count} dosya sir icerdigi icin pakete alinmadi:[/warn]",
        "en": "[warn]{count} files were left out of the package because they "
              "contain secrets:[/warn]",
    },
    "cli.large_skipped": {
        "tr": "[dim]{count} buyuk dosya atlandi[/dim]",
        "en": "[dim]{count} large files skipped[/dim]",
    },
    "cli.warnings": {"tr": "[warn]Uyarilar:[/warn]", "en": "[warn]Warnings:[/warn]"},
    "cli.delivery_package": {"tr": "Teslimat paketi", "en": "Delivery package"},
    "cli.assumption_prefix": {"tr": "varsayim: {text}", "en": "assumption: {text}"},
    "cli.blocking_count": {
        "tr": "[warn]{count} soru boru hattini durduruyor.[/warn]",
        "en": "[warn]{count} questions are stopping the pipeline.[/warn]",
    },
    "cli.answer_hint": {
        "tr": 'Cevaplayin: deerx answer {key} "..."',
        "en": 'Answer them: deerx answer {key} "..."',
    },
    "cli.no_such_question_hint": {
        "tr": "'{key}' diye bir soru yok. `deerx questions` ile bakin.",
        "en": "There is no question named '{key}'. Look with `deerx questions`.",
    },
    "cli.answered": {"tr": "{key} cevaplandi.", "en": "{key} was answered."},
    "cli.more_waiting": {
        "tr": "[warn]{count} soru daha bekliyor:[/warn] {keys}",
        "en": "[warn]{count} more questions are waiting:[/warn] {keys}",
    },
    "cli.no_artifacts": {
        "tr": "[warn]Henuz cikti yok.[/warn]",
        "en": "[warn]No artifacts yet.[/warn]",
    },
    "cli.directory": {
        "tr": "[dim]Dizin: {path}[/dim]",
        "en": "[dim]Directory: {path}[/dim]",
    },
    "cli.artifact_not_found": {
        "tr": "'{name}' bulunamadi. Mevcut: {available}",
        "en": "'{name}' not found. Available: {available}",
    },
    "cli.provider": {"tr": "Saglayici", "en": "Provider"},
    "cli.model_endpoint": {"tr": "Model ucu", "en": "Model endpoint"},
    "cli.undefined": {"tr": "tanimsiz", "en": "undefined"},
    "cli.connection": {"tr": "Baglanti", "en": "Connection"},
    "cli.model_pair": {
        "tr": "lead={lead} · worker={worker}",
        "en": "lead={lead} · worker={worker}",
    },
    "cli.defined": {"tr": "tanimli", "en": "defined"},
    "cli.add_to_env": {"tr": ".env icine ekleyin", "en": "add it to .env"},
    "cli.hint_fastembed": {
        "tr": "uv add fastembed (yerel gomme icin)",
        "en": "uv add fastembed (for local embeddings)",
    },
    "cli.hint_pypdf": {"tr": "uv add pypdf (PDF icin)", "en": "uv add pypdf (for PDF)"},
    "cli.hint_docx": {
        "tr": "uv add python-docx (DOCX icin)",
        "en": "uv add python-docx (for DOCX)",
    },
    "cli.hint_soup": {
        "tr": "uv add beautifulsoup4 (HTML icin)",
        "en": "uv add beautifulsoup4 (for HTML)",
    },
    "cli.hint_playwright": {
        "tr": "uv add playwright (JS sayfalari icin, opsiyonel)",
        "en": "uv add playwright (for JS pages, optional)",
    },
    "cli.installed": {"tr": "kurulu", "en": "installed"},
    "cli.kb_stats_fts": {
        "tr": "{documents} dokuman · {chunks} parca · FTS5={fts}",
        "en": "{documents} documents · {chunks} chunks · FTS5={fts}",
    },
    "cli.on": {"tr": "acik", "en": "on"},
    "cli.off": {"tr": "kapali", "en": "off"},
    "cli.unknown_phase": {
        "tr": "Bilinmeyen faz '{name}'. Secenekler: {options}",
        "en": "Unknown phase '{name}'. Options: {options}",
    },
    "cli.phase_order": {
        "tr": "'{start}' fazi '{end}' fazindan sonra geliyor.",
        "en": "Phase '{start}' comes after phase '{end}'.",
    },
    "cli.base_undefined": {"tr": "taban adres tanimsiz", "en": "base address undefined"},
    "cli.auth_refused": {
        "tr": "kimlik dogrulama reddedildi — OPENAI_API_KEY dogru mu?",
        "en": "authentication refused — is OPENAI_API_KEY correct?",
    },
    "cli.unreachable": {"tr": "ulasilamadi: {error}", "en": "unreachable: {error}"},
    "cli.model_not_served": {
        "tr": "sunulmayan model: {missing} · uctaki: {served}",
        "en": "model not served: {missing} · at the endpoint: {served}",
    },
    "cli.models_served": {
        "tr": "{count} model sunuluyor: {served}",
        "en": "{count} models served: {served}",
    },
    "cli.brief_missing": {
        "tr": "Brief dosyasi bulunamadi: {path}",
        "en": "Brief file not found: {path}",
    },
    "cli.answer_file_missing": {
        "tr": "Cevap dosyasi bulunamadi: {path}",
        "en": "Answer file not found: {path}",
    },
    "cli.run_summary": {"tr": "Kosu ozeti", "en": "Run summary"},
    "cli.total_cost": {
        "tr": "[cost]Toplam maliyet: ${amount}[/cost]",
        "en": "[cost]Total cost: ${amount}[/cost]",
    },
    "cli.run_counts": {
        "tr": "{requirements} gereksinim · {gaps} bosluk · {tasks} gorev "
              "· {artifacts} cikti",
        "en": "{requirements} requirements · {gaps} gaps · {tasks} tasks "
              "· {artifacts} artifacts",
    },
    "cli.artifacts_at": {
        "tr": "[dim]Ciktilar: {path}[/dim]",
        "en": "[dim]Artifacts: {path}[/dim]",
    },
    "cli.why": {"tr": "Neden: {text}", "en": "Why: {text}"},
    "cli.suggested_assumption": {
        "tr": "Onerilen varsayim: {text}",
        "en": "Suggested assumption: {text}",
    },
    "cli.to_answer": {
        "tr": "[bold]Cevaplamak icin:[/bold]",
        "en": "[bold]To answer:[/bold]",
    },
    "cli.answer_example": {
        "tr": '  deerx answer {key} "cevabiniz"',
        "en": '  deerx answer {key} "your answer"',
    },
    "cli.to_skip": {
        "tr": "[bold]Varsayimla gecmek icin:[/bold]",
        "en": "[bold]To move on with an assumption:[/bold]",
    },
    "cli.then_continue": {
        "tr": "Sonra kaldiginiz yerden devam edin:  deerx run --from <faz>",
        "en": "Then continue from where you left off:  deerx run --from <phase>",
    },
    "cli.needs_your_answer": {
        "tr": "Devam etmek icin cevabiniz gerekiyor",
        "en": "Your answer is needed to continue",
    },

    # -- Arac katmani: yol ve onay ---------------------------------------- #
    "tool.outside_workspace": {
        "tr": "Yol calisma alani disinda: {path}\nCalisma alani: {workspace}",
        "en": "The path is outside the workspace: {path}\nWorkspace: {workspace}",
    },
    "tool.path_missing": {"tr": "Yol bulunamadi: {path}", "en": "Path not found: {path}"},
    "tool.approval_denied": {
        "tr": "Kullanici '{action}' islemini reddetti.",
        "en": "The user refused the '{action}' operation.",
    },
    "tool.approval_needed": {"tr": "Onay gerekiyor:", "en": "Approval needed:"},
    "tool.approval_continue": {"tr": "Devam edilsin mi?", "en": "Continue?"},
    "tool.no_kb": {
        "tr": "Bu arac icin bilgi tabani baslatilmamis.",
        "en": "The knowledge base is not started for this tool.",
    },

    # -- Arac katmani: tarayici ------------------------------------------- #
    "browser.disabled": {
        "tr": "Tarayici kapali. Ayarlar > Web arastirma'yi acin ya da yalnizca "
              "kendi uygulamanizi acmak icin Ayarlar > Tarayici > "
              "'Ajan kendi uygulamasini acabilsin'.",
        "en": "The browser is off. Turn on Settings > Web research, or -- to open "
              "only your own application -- Settings > Browser > "
              "'Let the agent open its own application'.",
    },
    "browser.no_session": {
        "tr": "Tarayici oturumu bu baglamda kullanilamiyor.",
        "en": "The browser session is not available in this context.",
    },
    "browser.web_off": {
        "tr": "Web erisimi kapali (Ayarlar > Web arastirma).",
        "en": "Web access is off (Settings > Web research).",
    },
    "browser.decoy_results": {
        "tr": "{engine} sorguyla alakasiz bir sonuc kumesi dondurdu "
              "(hicbir sonucta sorgunun terimleri gecmiyor). Bazi uclar "
              "otomatik tarayiciyi tespit edince engellemek yerine sahte "
              "sonuc veriyor; bunlar CEVAP DEGILDIR ve atildi.",
        "en": "{engine} returned a result set unrelated to the query (not one "
              "result contains any of the query's terms). Some endpoints serve "
              "decoy results instead of blocking when they detect an automated "
              "browser; these are NOT AN ANSWER and were discarded.",
    },
    "browser.search_failed": {
        "tr": "Arama yapilamadi -- bu bir 'sonuc bulunamadi' cevabi DEGILDIR. "
              "Denenen: {problems}\n\nArama motorlari otomatik tarayicilari sik "
              "sik engelliyor. Kalici cozum: Ayarlar > Web arastirma bolumunden "
              "Brave ya da Tavily anahtari tanimlayin.\n"
              "Bu konuda bilgi sahibi oldugunuzu VARSAYMAYIN; bir sey "
              "bulamadiginizi degil, ARAMANIN CALISMADIGINI bildirin. "
              "Bildiginiz bir adres varsa `browse_page` ile dogrudan acabilirsiniz.",
        "en": "The search could not be run -- this is NOT a 'no results' answer. "
              "Tried: {problems}\n\nSearch engines often block automated "
              "browsers. The lasting fix: set a Brave or Tavily key under "
              "Settings > Web research.\n"
              "Do NOT assume you know about this subject; report that THE SEARCH "
              "DID NOT WORK, not that you found nothing. If you know an address, "
              "you can open it directly with `browse_page`.",
    },
    "browser.no_page": {
        "tr": "Once `browse_page` ya da `web_search` ile bir sayfa acin.",
        "en": "Open a page first with `browse_page` or `web_search`.",
    },
    "browser.no_element": {
        "tr": "'{ref}' diye bir oge yok. Sayfa degismis olabilir; "
              "`browser_snapshot` ile listeyi tazeleyin.",
        "en": "There is no element '{ref}'. The page may have changed; refresh "
              "the list with `browser_snapshot`.",
    },
    "browser.no_field": {
        "tr": "'{ref}' diye bir alan yok. `browser_snapshot` ile tazeleyin.",
        "en": "There is no field '{ref}'. Refresh it with `browser_snapshot`.",
    },
    "browser.click_failed": {"tr": "Tiklanamadi: {error}", "en": "Could not click: {error}"},
    "browser.type_failed": {"tr": "Yazilamadi: {error}", "en": "Could not type: {error}"},
    "browser.back_failed": {
        "tr": "Geri gidilemedi: {error}",
        "en": "Could not go back: {error}",
    },
    "browser.screenshot_failed": {
        "tr": "Ekran goruntusu alinamadi: {error}",
        "en": "Could not take the screenshot: {error}",
    },
    "browser.blocked_by_policy": {
        "tr": "Tiklama {url} adresine gitti ve politika bunu engelledi.",
        "en": "The click went to {url} and the policy blocked it.",
    },
    "browser.no_open_page": {
        "tr": "Acik bir sayfa yok. Once `preview_open` ya da `browse_page`.",
        "en": "No page is open. Use `preview_open` or `browse_page` first.",
    },
    "browser.preview_off": {
        "tr": "Yerel onizleme kapali. Ayarlar > Tarayici > "
              "'Ajan kendi uygulamasini acabilsin' secenegini acin.",
        "en": "Local preview is off. Turn on Settings > Browser > "
              "'Let the agent open its own application'.",
    },
    "browser.bad_port": {"tr": "Gecersiz port: {port}", "en": "Invalid port: {port}"},
    "browser.port_range": {
        "tr": "Port araligi disinda: {port}",
        "en": "Port out of range: {port}",
    },
    "browser.preview_failed": {
        "tr": "{origin} acilamadi: {error}\nUygulama gercekten calisiyor mu? "
              "`start_service` ile baslattiginizdan ve portun dogru oldugundan "
              "emin olun; `service_log` ile sunucunun ne dedigine bakin.",
        "en": "Could not open {origin}: {error}\nIs the application really "
              "running? Make sure you started it with `start_service` and that "
              "the port is right; look at what the server said with "
              "`service_log`.",
    },
    "browser.opened": {"tr": "acildi: {url}", "en": "opened: {url}"},
    "browser.clicked": {"tr": "tiklandi: {ref}", "en": "clicked: {ref}"},
    "browser.typed": {"tr": "yazildi: {ref}", "en": "typed: {ref}"},
    "browser.typed_ok": {"tr": "{ref} alanina yazildi.", "en": "Typed into {ref}."},
    "browser.enter_pressed": {"tr": " Enter'a basildi.", "en": " Enter was pressed."},
    "browser.preview_opened": {
        "tr": "onizleme acildi: {origin}",
        "en": "preview opened: {origin}",
    },

    # -- Arac katmani: dosya sistemi -------------------------------------- #
    "fs.is_a_dir": {
        "tr": "{path} bir dizin; `list_dir` kullanin.",
        "en": "{path} is a directory; use `list_dir`.",
    },
    "fs.too_large": {
        "tr": "{path} cok buyuk ({size} bayt).",
        "en": "{path} is too large ({size} bytes).",
    },
    "fs.not_text": {
        "tr": "{path} metin olarak okunamadi: {error}",
        "en": "{path} could not be read as text: {error}",
    },
    "fs.not_found_in_file": {
        "tr": "Aranan metin {path} icinde bulunamadi. Once `read_file` ile tam "
              "icerigi dogrulayin.",
        "en": "The text was not found in {path}. Confirm the exact content with "
              "`read_file` first.",
    },
    "fs.not_unique": {
        "tr": "Aranan metin {count} kez geciyor. Daha fazla baglam ekleyin veya "
              "replace_all=true kullanin.",
        "en": "The text occurs {count} times. Add more context or use "
              "replace_all=true.",
    },
    "fs.not_a_dir": {
        "tr": "{path} bir dizin degil.",
        "en": "{path} is not a directory.",
    },
    "fs.bad_regex": {"tr": "Gecersiz regex: {error}", "en": "Invalid regex: {error}"},

    # -- Arac katmani: bilgi ve proje ------------------------------------- #
    "tool.source_not_found": {
        "tr": "'{source}' bulunamadi. Mevcut: {available}",
        "en": "'{source}' not found. Available: {available}",
    },
    "tool.bad_key": {
        "tr": "Gecersiz anahtar '{key}'. Bicim: {prefix}-001",
        "en": "Invalid key '{key}'. Format: {prefix}-001",
    },
    "tool.no_such_task": {
        "tr": "'{key}' diye bir gorev yok.",
        "en": "There is no task named '{key}'.",
    },
    "tool.task_updated": {
        "tr": "{key} durumu '{status}' olarak guncellendi.",
        "en": "{key} status updated to '{status}'.",
    },
    "tool.bad_artifact_name": {
        "tr": "Gecersiz cikti adi: {name}",
        "en": "Invalid artifact name: {name}",
    },
    "tool.saved_keys": {
        "tr": "Kaydedildi: {keys} (toplam {count}).",
        "en": "Saved: {keys} ({count} in total).",
    },
    "tool.saved_tasks": {"tr": "Kaydedildi: {keys}.", "en": "Saved: {keys}."},
    "tool.dangling_deps": {
        "tr": "\nUYARI: tanimsiz bagimlilik anahtarlari var: {keys}",
        "en": "\nWARNING: there are undefined dependency keys: {keys}",
    },
    "tool.recorded_count": {
        "tr": "{count} {kind} kaydedildi",
        "en": "{count} {kind} recorded",
    },
    "tool.artifact_written": {
        "tr": "{name} yazildi ({size} karakter)",
        "en": "{name} written ({size} characters)",
    },
    "tool.error_prefix": {"tr": "HATA", "en": "ERROR"},
    "tool.unexpected": {
        "tr": "Arac calistirilirken beklenmeyen hata: {name}",
        "en": "Unexpected error while running the tool: {name}",
    },

    # -- Arac katmani: web ------------------------------------------------ #
    "web.scheme_only": {
        "tr": "Yalnizca http/https destekleniyor: {url}",
        "en": "Only http/https is supported: {url}",
    },
    "web.bad_url": {"tr": "Gecersiz URL: {url}", "en": "Invalid URL: {url}"},
    "web.dns_failed": {
        "tr": "Alan adi cozulemedi: {host} ({error})",
        "en": "Could not resolve the host name: {host} ({error})",
    },
    "web.already_failed": {
        "tr": "\n\nBu adresi {count} kez denediniz ve her seferinde ayni sekilde "
              "dustu. Calismaya baslamayacak: BIR DAHA DENEMEYIN. Baska bir "
              "kaynak bulun ya da `web_search` ile arayin.",
        "en": "\n\nYou have tried this address {count} times and it failed the "
              "same way each time. It will not start working: DO NOT TRY IT "
              "AGAIN. Find another source or search with `web_search`.",
    },
    "web.fetch_failed": {
        "tr": "Sayfa alinamadi: {error}",
        "en": "Could not fetch the page: {error}",
    },
    "web.disabled": {"tr": "Web erisimi kapali.", "en": "Web access is off."},
    "web.no_playwright": {
        "tr": "playwright kurulu degil. Kurulum:\n"
              "  uv add playwright\n"
              "  playwright install chromium\n"
              "Alternatif: `fetch_url` veya sunucu tarafi `web_fetch` aracini kullanin.",
        "en": "playwright is not installed. To install:\n"
              "  uv add playwright\n"
              "  playwright install chromium\n"
              "Alternatively use `fetch_url` or the server-side `web_fetch` tool.",
    },

    # -- Kayit turleri (olay akisindaki sayimlar) ------------------------- #
    "kind.requirements": {"tr": "gereksinim", "en": "requirements"},
    "kind.tasks": {"tr": "gorev", "en": "tasks"},

    # -- Web: istek ve ayarlar -------------------------------------------- #
    "api.bad_json": {"tr": "Gecersiz JSON govdesi.", "en": "Invalid JSON body."},
    "api.body_not_object": {
        "tr": "Istek govdesi bir nesne olmali.",
        "en": "The request body must be an object.",
    },
    "api.unknown_setting": {
        "tr": "Bilinmeyen ayar: {name}",
        "en": "Unknown setting: {name}",
    },
    "api.unknown_section": {
        "tr": "Bilinmeyen bolum: {name}",
        "en": "Unknown section: {name}",
    },
    "api.run_not_stopping": {
        "tr": "Kosu {seconds}s icinde durmadi; veritabani acik birakiliyor",
        "en": "The run did not stop within {seconds}s; leaving the database open",
    },

    # -- Web: planlar ------------------------------------------------------ #
    "api.plan_needs_name": {"tr": "Plana bir ad verin.", "en": "Give the plan a name."},
    "api.no_such_plan": {
        "tr": "'{id}' diye bir plan yok.",
        "en": "There is no plan with id '{id}'.",
    },
    "api.bad_plan_status": {
        "tr": "Gecersiz plan durumu: {status}",
        "en": "Invalid plan status: {status}",
    },
    "api.plan_locked": {
        "tr": "Kosu devam ederken plan silinemez.",
        "en": "A plan cannot be deleted while a run is in progress.",
    },
    "api.last_plan": {
        "tr": "Tek kalan plan silinemez.",
        "en": "The only remaining plan cannot be deleted.",
    },
    "api.plan_created": {"tr": "plan olusturuldu: {name}", "en": "plan created: {name}"},
    "api.plan_active": {"tr": "etkin plan: {id}", "en": "active plan: {id}"},
    "api.plan_deleted": {
        "tr": "plan silindi ({count} gorev)",
        "en": "plan deleted ({count} tasks)",
    },

    # -- Web: kullanicilar ------------------------------------------------- #
    "api.wrong_current_password": {
        "tr": "Mevcut parola hatali.",
        "en": "The current password is wrong.",
    },
    "api.admin_only": {
        "tr": "Bu islem icin yonetici yetkisi gerekir.",
        "en": "This operation requires administrator rights.",
    },
    "api.user_not_found": {"tr": "Kullanici bulunamadi.", "en": "User not found."},
    "api.cannot_disable_self": {
        "tr": "Kendi hesabinizi kapatamazsiniz.",
        "en": "You cannot disable your own account.",
    },
    "api.cannot_delete_self": {
        "tr": "Kendi hesabinizi silemezsiniz.",
        "en": "You cannot delete your own account.",
    },
    "api.user_created": {
        "tr": "olusturuldu: {name} ({role})",
        "en": "created: {name} ({role})",
    },
    "api.user_activated": {"tr": "{name} acildi", "en": "{name} enabled"},
    "api.user_deactivated": {"tr": "{name} kapatildi", "en": "{name} disabled"},
    "api.password_reset": {
        "tr": "{name} parolasi sifirlandi",
        "en": "password reset for {name}",
    },
    "api.sessions_closed": {
        "tr": "{name}: {count} oturum kapatildi",
        "en": "{name}: {count} sessions closed",
    },
    "api.user_removed": {"tr": "silindi: {name}", "en": "deleted: {name}"},

    # -- Web: gorevler, bilgi tabani, dosyalar ----------------------------- #
    "api.no_such_task": {
        "tr": "'{key}' diye bir gorev yok.",
        "en": "There is no task named '{key}'.",
    },
    "api.bad_status": {
        "tr": "Gecersiz durum: {status}. Secenekler: {options}",
        "en": "Invalid status: {status}. Options: {options}",
    },
    "api.path_not_found": {"tr": "Yol bulunamadi: {path}", "en": "Path not found: {path}"},
    "api.source_required": {"tr": "source alani gerekli.", "en": "The source field is required."},
    "api.upload_locked": {
        "tr": "Kosu devam ederken dosya yuklenemez.",
        "en": "Files cannot be uploaded while a run is in progress.",
    },
    "api.need_file_name": {
        "tr": "Gecerli bir dosya adi verin (`name` parametresi).",
        "en": "Give a valid file name (the `name` parameter).",
    },
    "api.unsupported_suffix": {
        "tr": "'{suffix}' desteklenmiyor. Desteklenenler: {supported}",
        "en": "'{suffix}' is not supported. Supported: {supported}",
    },
    "api.empty_file": {"tr": "Bos dosya.", "en": "Empty file."},
    "api.file_too_large": {
        "tr": "Dosya cok buyuk ({size} bayt). Sinir: {limit}.",
        "en": "The file is too large ({size} bytes). Limit: {limit}.",
    },
    "api.zip_only": {
        "tr": "Yalnizca .zip dosyalari indirilebilir.",
        "en": "Only .zip files can be downloaded.",
    },
    "api.not_found": {"tr": "'{name}' bulunamadi.", "en": "'{name}' not found."},
    "api.file_missing": {"tr": "Dosya diskte yok: {path}", "en": "File not on disk: {path}"},
    "api.removed_chunks": {
        "tr": "kaldirildi: {source} ({count} parca)",
        "en": "removed: {source} ({count} chunks)",
    },
    "api.upload_received": {
        "tr": "{name} alindi ({size} bayt)",
        "en": "{name} received ({size} bytes)",
    },
    "api.task_status": {"tr": "{key} -> {status}", "en": "{key} -> {status}"},

    # -- Web: sorular, is akislari, kosular -------------------------------- #
    "api.empty_answer": {
        "tr": "Bos cevap. Varsayimla gecmek icin action='skip' kullanin.",
        "en": "Empty answer. Use action='skip' to move on with an assumption.",
    },
    "api.unknown_action": {
        "tr": "Bilinmeyen islem: {action}. 'answer' veya 'skip' olmali.",
        "en": "Unknown action: {action}. It must be 'answer' or 'skip'.",
    },
    "api.no_such_question": {
        "tr": "'{key}' diye bir soru yok.",
        "en": "There is no question named '{key}'.",
    },
    "api.no_such_workflow": {
        "tr": "'{id}' diye bir is akisi yok.",
        "en": "There is no workflow with id '{id}'.",
    },
    "api.no_such_run": {
        "tr": "'{id}' diye bir kosu yok.",
        "en": "There is no run with id '{id}'.",
    },
    "api.phases_must_be_list": {
        "tr": "phases bir dizi olmali.",
        "en": "phases must be an array.",
    },
    "api.approval_gone": {
        "tr": "Onay istegi bulunamadi veya suresi doldu.",
        "en": "The approval request was not found or has expired.",
    },
    "api.run_busy": {
        "tr": "Zaten calisan bir kosu var. Once onu durdurun.",
        "en": "A run is already in progress. Stop it first.",
    },
    "api.no_phase_selected": {
        "tr": "Calistirilacak faz secilmedi.",
        "en": "No phase was selected to run.",
    },
    "api.run_crashed": {
        "tr": "Kosu beklenmeyen bir hatayla sonlandi",
        "en": "The run ended with an unexpected error",
    },
    "api.unknown_phase": {
        "tr": "Bilinmeyen faz '{name}'. Secenekler: {options}",
        "en": "Unknown phase '{name}'. Options: {options}",
    },
    "api.unknown_phase_plain": {
        "tr": "Bilinmeyen faz. Secenekler: {options}",
        "en": "Unknown phase. Options: {options}",
    },
    "api.phase_order": {
        "tr": "'{start}' fazi '{end}' fazindan sonra geliyor.",
        "en": "Phase '{start}' comes after phase '{end}'.",
    },
    "api.retry_no_phases": {
        "tr": "#{seq} kosusunun faz listesi bos; tekrar calistirilamaz.",
        "en": "Run #{seq} has no recorded phases, so it cannot be re-run.",
    },
    "api.retry_phase_not_in_run": {
        "tr": "'{phase}' fazi #{seq} kosusunda yok. O kosunun adimlari: {phases}",
        "en": "Phase '{phase}' was not part of run #{seq}. Its steps were: {phases}",
    },
    "api.retry_nothing_failed": {
        "tr": "#{seq} kosusunda basarisiz adim yok. Tekrar icin bir adim secin.",
        "en": "No step failed in run #{seq}. Pick a step to re-run from.",
    },

    # -- Web: olay akisindaki aktorler ------------------------------------- #
    "actor.user": {"tr": "kullanici", "en": "user"},
    "actor.task": {"tr": "gorev", "en": "task"},
    "actor.upload": {"tr": "yukleme", "en": "upload"},

    # -- Web: sunucu acilis ciktisi ---------------------------------------- #
    "serve.no_users_remote": {
        "tr": "{host} adresinde kullanicisiz sunucu baslatilamaz.\n"
              "Once yerelde bir yonetici olusturun:\n"
              "  deerx serve            (127.0.0.1; kurulum jetonu konsola basilir)\n"
              "  deerx user add <ad> --admin",
        "en": "A server with no users cannot be started on {host}.\n"
              "Create an administrator locally first:\n"
              "  deerx serve            (127.0.0.1; the setup token is printed to the console)\n"
              "  deerx user add <name> --admin",
    },
    "serve.no_users_local": {
        "tr": "\n[warn]Kullanici tanimli degil - kimlik dogrulama KAPALI.[/warn]\n"
              "Hesap olusturmak icin kurulum ekraninda bu jetonu kullanin:\n"
              "  [bold]{token}[/bold]\n",
        "en": "\n[warn]No users are defined - authentication is OFF.[/warn]\n"
              "Use this token on the setup screen to create an account:\n"
              "  [bold]{token}[/bold]\n",
    },
    "serve.exposed_warning": {
        "tr": "[warn]UYARI:[/warn] sunucu {host} adresinde dinliyor. DeerX dosya "
              "yazabilir ve kabuk komutu calistirabilir; bu adresi guvenilmeyen "
              "aglara acmayin. Duz HTTP kullaniyorsaniz oturum cerezi acik "
              "metin tasinir: onune TLS sonlandiran bir vekil koyun.",
        "en": "[warn]WARNING:[/warn] the server is listening on {host}. DeerX can "
              "write files and run shell commands; do not expose this address to "
              "untrusted networks. Over plain HTTP the session cookie travels in "
              "clear text: put a TLS-terminating proxy in front.",
    },
    "serve.listening": {
        "tr": "[ok]DeerX web arayuzu:[/ok] {url}",
        "en": "[ok]DeerX web interface:[/ok] {url}",
    },
    "serve.workspace": {
        "tr": "[dim]Calisma alani: {path}[/dim]",
        "en": "[dim]Workspace: {path}[/dim]",
    },
    "serve.login_required": {
        "tr": "[dim]Giris gerekiyor.[/dim]",
        "en": "[dim]Sign-in required.[/dim]",
    },

    # -- Kimlik dogrulama --------------------------------------------------- #
    "auth.password_too_short": {
        "tr": "Parola en az {min} karakter olmali.",
        "en": "The password must be at least {min} characters.",
    },
    "auth.password_spaces": {
        "tr": "Parola bosluk ile baslayamaz veya bitemez.",
        "en": "The password cannot start or end with a space.",
    },
    "auth.password_common": {
        "tr": "Bu parola bilinen listelerde: her tarama botunun ilk denedigi "
              "kombinasyonlardan biri. Sunucuyu aga acacaksaniz mutlaka degistirin.",
        "en": "This password is on the known lists: one of the first combinations "
              "every scanning bot tries. Change it before you expose the server to "
              "a network.",
    },
    "auth.password_short_warning": {
        "tr": "Kisa parola: aga acilan bir sunucu icin en az 12 karakter onerilir.",
        "en": "Short password: at least 12 characters is recommended for a server "
              "open to a network.",
    },
    "auth.already_configured": {
        "tr": "Kurulum tamamlanmis; yeni yoneticiyi mevcut bir yonetici olusturur.",
        "en": "Setup is complete; a new administrator is created by an existing one.",
    },
    "auth.bad_setup_token": {
        "tr": "Kurulum jetonu gecersiz. Sunucunun konsoluna bakin.",
        "en": "The setup token is invalid. Look at the server's console.",
    },
    "auth.first_admin": {
        "tr": "Ilk yonetici olusturuldu: {name}",
        "en": "First administrator created: {name}",
    },
    "auth.bad_username": {
        "tr": "Kullanici adi 3-32 karakter olmali; kucuk harf, rakam, nokta, "
              "tire ve alt tire kullanilabilir.",
        "en": "The user name must be 3-32 characters; lower-case letters, digits, "
              "dots, hyphens and underscores are allowed.",
    },
    "auth.bad_role": {"tr": "Gecersiz rol: {role}", "en": "Invalid role: {role}"},
    "auth.user_not_found": {"tr": "Kullanici bulunamadi.", "en": "User not found."},
    "auth.master_role": {
        "tr": "Ana yoneticinin rolu dusurulemez.",
        "en": "The primary administrator's role cannot be lowered.",
    },
    "auth.master_disable": {
        "tr": "Ana yonetici kapatilamaz.",
        "en": "The primary administrator cannot be disabled.",
    },
    "auth.master_delete": {
        "tr": "Ana yonetici silinemez.",
        "en": "The primary administrator cannot be deleted.",
    },
    "auth.locked_out": {
        "tr": "Cok fazla basarisiz deneme. {seconds} saniye sonra tekrar deneyin.",
        "en": "Too many failed attempts. Try again in {seconds} seconds.",
    },
    "auth.bad_credentials": {
        "tr": "Kullanici adi veya parola hatali.",
        "en": "Wrong user name or password.",
    },
    "auth.account_disabled": {
        "tr": "Bu hesap kapatilmis. Yoneticinize basvurun.",
        "en": "This account is disabled. Contact your administrator.",
    },
    "auth.migration_is_active": {
        "tr": "Veritabani gecisi: users.is_active ekleniyor",
        "en": "Database migration: adding users.is_active",
    },
    "auth.audit_failed": {
        "tr": "Denetim gunlugune yazilamadi ({action}): {error}",
        "en": "Could not write to the audit log ({action}): {error}",
    },

    # -- Kurulum ve yapilandirma ------------------------------------------ #
    "setup.prompt_missing": {
        "tr": "Prompt bulunamadi: {name} ({path})",
        "en": "Prompt not found: {name} ({path})",
    },
    "setup.no_anthropic_key": {
        "tr": "ANTHROPIC_API_KEY tanimli degil. .env dosyasina ekleyin veya "
              "ortam degiskeni olarak disari aktarin.",
        "en": "ANTHROPIC_API_KEY is not set. Add it to the .env file or export "
              "it as an environment variable.",
    },
    "setup.toml_unreadable": {
        "tr": "{path} okunamadi: {error}",
        "en": "{path} could not be read: {error}",
    },
    "setup.unknown_settings": {
        "tr": "{file} icinde taninmayan ayar: {keys} -- bu satirlar yok sayiliyor.",
        "en": "Unrecognised setting in {file}: {keys} -- these lines are ignored.",
    },
    "setup.did_you_mean": {
        "tr": " (bunu mu demek istediniz: {suggestion}?)",
        "en": " (did you mean: {suggestion}?)",
    },
    "setup.timeout_too_low": {
        "tr": "max_tokens={tokens} yerel bir ucta ~{minutes} dakika surer; "
              "request_timeout_seconds={timeout} bundan kucuk, istek yarida "
              "kesilebilir.",
        "en": "max_tokens={tokens} takes about {minutes} minutes on a local "
              "endpoint; request_timeout_seconds={timeout} is smaller than that, "
              "so the request may be cut off.",
    },
    "setup.unknown_provider": {
        "tr": "Bilinmeyen saglayici: {provider}. 'anthropic' veya 'openai' olmali.",
        "en": "Unknown provider: {provider}. It must be 'anthropic' or 'openai'.",
    },
    "setup.no_openai_package": {
        "tr": "OpenAI-uyumlu saglayici icin `openai` paketi gerekli:\n  uv add openai",
        "en": "The `openai` package is required for an OpenAI-compatible "
              "provider:\n  uv add openai",
    },
    "setup.no_base_url": {
        "tr": "OpenAI-uyumlu saglayici icin `openai_base_url` tanimlanmali.\n"
              "Ornek (yerel vLLM):  DEERX_OPENAI_BASE_URL=http://127.0.0.1:8008/v1",
        "en": "`openai_base_url` must be set for an OpenAI-compatible provider.\n"
              "Example (local vLLM):  DEERX_OPENAI_BASE_URL=http://127.0.0.1:8008/v1",
    },
    "setup.no_playwright_driver": {
        "tr": "Tarayici surucusu kurulu degil. Kurulum:\n"
              "  uv add 'deerx[browser]'\n"
              "Sistemde kurulu Chrome kullanilacagi icin ayrica tarayici "
              "indirmeniz gerekmez.",
        "en": "The browser driver is not installed. To install:\n"
              "  uv add 'deerx[browser]'\n"
              "The Chrome already on your system is used, so you do not need to "
              "download a browser separately.",
    },
    "setup.no_browser": {
        "tr": "Tarayici bulunamadi (tercih: {channel}). Ayarlar > Tarayici "
              "bolumunden baska bir secenek deneyin.",
        "en": "No browser found (preferred: {channel}). Try another option under "
              "Settings > Browser.",
    },
    "setup.browser_launch_failed": {
        "tr": "{label} baslatilamadi: {error}",
        "en": "Could not start {label}: {error}",
    },
    "setup.browser_started": {
        "tr": "Tarayici basladi: {label} (vekil :{port})",
        "en": "Browser started: {label} (proxy :{port})",
    },
    "setup.proxy_error": {
        "tr": "Vekil baglantisi hatayla bitti",
        "en": "The proxy connection ended with an error",
    },
    "setup.no_fastembed": {
        "tr": "Gomme icin `fastembed` gerekli. Kurulum:\n  uv add fastembed\n"
              "Alternatif olarak deerx.toml icinde "
              '[deerx.rag] embedding_provider = \"hash\" yapabilirsiniz '
              "(yalnizca test icin; arama kalitesi dusuktur).",
        "en": "`fastembed` is required for embeddings. To install:\n"
              "  uv add fastembed\n"
              "Alternatively you can set "
              '[deerx.rag] embedding_provider = \"hash\" in deerx.toml '
              "(for testing only; search quality is poor).",
    },
    "setup.model_unsupported": {
        "tr": "Gomme modeli '{model}' fastembed tarafindan desteklenmiyor.\n\n"
              "Cok dilli (Turkce dahil) secenekler:\n{options}\n\n"
              "deerx.toml -> [deerx.rag] embedding_model ve embedding_dim "
              "degerlerini birlikte guncelleyin.",
        "en": "The embedding model '{model}' is not supported by fastembed.\n\n"
              "Multilingual options (Turkish included):\n{options}\n\n"
              "Update deerx.toml -> [deerx.rag] embedding_model and embedding_dim "
              "together.",
    },
    "setup.dim_mismatch": {
        "tr": "Gomme boyutu ayarla uyusmuyor ({actual} != {configured}); model "
              "degeri kullaniliyor.",
        "en": "The embedding dimension does not match the setting ({actual} != "
              "{configured}); the model's value is used.",
    },
    "setup.model_changed": {
        "tr": "Gomme modeli degismis. Depodaki vektorler {stored} boyutlu, "
              "'{model}' modeli {dim} boyut uretiyor.\n"
              "Bilgi tabanini yeniden olusturun:\n"
              "  deerx ingest --force\n"
              "veya deerx.toml icindeki embedding_model degerini geri alin.",
        "en": "The embedding model has changed. The stored vectors are {stored}-"
              "dimensional; the '{model}' model produces {dim}.\n"
              "Rebuild the knowledge base:\n"
              "  deerx ingest --force\n"
              "or restore the embedding_model value in deerx.toml.",
    },
    "setup.stored_dim_mismatch": {
        "tr": "Depodaki vektor boyutu ({stored}) sorgu boyutundan ({query}) "
              "farkli. Gomme modeli degistiyse `deerx ingest --reindex` gerekir.",
        "en": "The stored vector dimension ({stored}) differs from the query's "
              "({query}). If the embedding model changed, `deerx ingest "
              "--reindex` is needed.",
    },
    "setup.no_fts": {
        "tr": "FTS5 kullanilamiyor ({error}); sozcuksel arama LIKE ile yapilacak.",
        "en": "FTS5 is not available ({error}); lexical search will use LIKE.",
    },
    "setup.unreadable": {
        "tr": "{name} okunamadi: {error}",
        "en": "{name} could not be read: {error}",
    },
    "setup.migration": {
        "tr": "Veritabani gecisi: {table}.{column} ekleniyor",
        "en": "Database migration: adding {table}.{column}",
    },

    # -- LLM --------------------------------------------------------------- #
    "llm.budget_exceeded": {
        "tr": "Maliyet tavani asildi: ${spent} > ${limit}. deerx.toml icindeki "
              "cost_limit_usd degerini yukseltin.",
        "en": "The cost ceiling was exceeded: ${spent} > ${limit}. Raise "
              "cost_limit_usd in deerx.toml.",
    },
    "llm.api_error": {
        "tr": "Claude API hatasi ({status}): {message}",
        "en": "Claude API error ({status}): {message}",
    },
    "llm.connection_error": {
        "tr": "Claude API baglanti hatasi: {error}",
        "en": "Claude API connection error: {error}",
    },
    "llm.kwargs_exhausted": {
        "tr": "Model cagrisi tekrarlanan parametre hatalari nedeniyle yapilamadi.",
        "en": "The model call could not be made because of repeated parameter errors.",
    },
    "llm.context_overflow": {
        "tr": "Istek {model} ucunun baglam penceresine sigmiyor: pencere {window} "
              "token, girdi ~{input} token. Gecmisi kisaltin ya da daha genis "
              "pencereli bir model secin.",
        "en": "The request does not fit the context window of the {model} "
              "endpoint: the window is {window} tokens, the input about {input} "
              "tokens. Shorten the history or pick a model with a wider window.",
    },
    "llm.call_setup_failed": {
        "tr": "Model cagrisi kurulamadi: {error}",
        "en": "Could not set up the model call: {error}",
    },
    "llm.moved_kwarg": {
        "tr": "Bilinmeyen SDK parametresi extra_body icine tasindi: {name}",
        "en": "Unknown SDK parameter moved into extra_body: {name}",
    },

    # -- Boru hatti -------------------------------------------------------- #
    "pipeline.reclaimed": {
        "tr": "yarida kalmis {count} gorev yeniden kuyruga alindi: {keys}",
        "en": "{count} unfinished tasks were re-queued: {keys}",
    },
    "pipeline.reclaimed_log": {
        "tr": "Yarida kalmis {count} gorev yeniden kuyruga alindi",
        "en": "{count} unfinished tasks were re-queued",
    },
    "pipeline.not_ready": {
        "tr": "proje teslime hazir degil",
        "en": "the project is not ready for delivery",
    },
    "pipeline.goal_changed": {
        "tr": "hedef degismis; onceki sonuc bu hedefe ait degil, tekrar kosuluyor",
        "en": "the goal has changed; the previous result belongs to a different "
              "goal, running again",
    },
    "pipeline.package_written": {
        "tr": "Teslimat paketi yazildi: {path} ({count} dosya)",
        "en": "Delivery package written: {path} ({count} files)",
    },
    "pipeline.package_unreadable": {
        "tr": "Paket okunamadi ({name}): {error}",
        "en": "The package could not be read ({name}): {error}",
    },
    "actor.delivery": {"tr": "teslimat", "en": "delivery"},

    # -- Teslimat hazirlik denetimi ---------------------------------- #
    "package.plan_empty": {
        "tr": "Plan bos: paketlenecek bir is tanimlanmamis.",
        "en": "The plan is empty: no work has been defined to package.",
    },
    "package.failed_tasks": {
        "tr": "Basarisiz gorev var: {keys}",
        "en": "There are failed tasks: {keys}",
    },
    "package.unfinished_tasks": {
        "tr": "{count} gorev tamamlanmamis: {keys}{more}",
        "en": "{count} tasks are unfinished: {keys}{more}",
    },
    "package.open_gaps": {
        "tr": "{count} kritik/yuksek bosluk acik: {keys}{more}",
        "en": "{count} critical/high gaps are still open: {keys}{more}",
    },
    "package.blocking_questions": {
        "tr": "Cevaplanmamis bloke edici soru var: {keys}",
        "en": "There are unanswered blocking questions: {keys}",
    },
    "package.phase_not_run": {
        "tr": "{label} fazi calistirilmamis (durum: {status}).",
        "en": "The {label} phase has not run (status: {status}).",
    },

    # -- Ayarlar ve kosu yasaklari ---------------------------------------- #
    "api.models_locked": {
        "tr": "Kosu devam ederken model ayarlari degistirilemez.",
        "en": "Model settings cannot be changed while a run is in progress.",
    },
    "run.reclaimed_error": {
        "tr": "Sunucu yeniden baslatildi; kosu yarida kesildi.",
        "en": "The server restarted; the run was cut short.",
    },
    "api.invalid_choice": {
        "tr": "gecersiz deger '{value}'. Secenekler: {allowed}",
        "en": "invalid value '{value}'. Options: {allowed}",
    },
    "mcp.unknown_phase": {
        "tr": "HATA: bilinmeyen faz '{phase}'. Secenekler: {allowed}",
        "en": "ERROR: unknown phase '{phase}'. Options: {allowed}",
    },
    "pipeline.skipped_assumption": {
        "tr": "**Atlandi.** Su varsayimla ilerleyin: {assumption}",
        "en": "**Skipped.** Proceed with this assumption: {assumption}",
    },
    "pipeline.own_assumption": {
        "tr": "kendi makul varsayiminizi kurun ve belirtin",
        "en": "form your own reasonable assumption and state it",
    },
    "api.sandbox_locked": {
        "tr": "Kosu devam ederken yalitim ayarlari degistirilemez: kabini "
              "yeniden kurmak calisan konteyneri siler.",
        "en": "Isolation settings cannot be changed while a run is in "
              "progress: rebuilding the sandbox destroys the running "
              "container.",
    },
    "api.ingest_locked": {
        "tr": "Kosu devam ederken indeksleme yapilamaz.",
        "en": "Indexing cannot run while a run is in progress.",
    },
    "api.package_locked": {
        "tr": "Kosu devam ederken paketleme yapilamaz.",
        "en": "Packaging cannot run while a run is in progress.",
    },
    "api.settings_updated": {
        "tr": "guncellendi: {changed}",
        "en": "updated: {changed}",
    },
    "actor.settings": {"tr": "ayarlar", "en": "settings"},
    "record.defined": {"tr": "tanimlandi", "en": "set"},
    "record.cleared": {"tr": "silindi", "en": "cleared"},

    # -- Kosu yasam dongusu ------------------------------------------------ #
    "run.reclaimed": {
        "tr": "yarida kalmis {count} kosu kapatildi: {ids}",
        "en": "{count} unfinished runs were closed: {ids}",
    },
    "run.reclaimed_log": {
        "tr": "Yarida kalmis {count} kosu kapatildi",
        "en": "{count} unfinished runs were closed",
    },
    "run.stopped": {"tr": "kosu durduruldu", "en": "run stopped"},
    "run.finished": {
        "tr": "kosu bitti: {status} · ${cost} · {seconds}s",
        "en": "run finished: {status} · ${cost} · {seconds}s",
    },
    "run.workflow_migration": {
        "tr": "Gecis: {runs} kosu {workflows} is akisina baglandi",
        "en": "Migration: {runs} runs attached to {workflows} workflows",
    },

    # -- Tarayici oturumu -------------------------------------------------- #
    "browser.idle_closed": {
        "tr": "Tarayici {seconds} sn bos kaldi, kapatiliyor",
        "en": "The browser was idle for {seconds}s; closing it",
    },
    "browser.page_failed": {
        "tr": "Sayfa acilamadi: {error}",
        "en": "The page could not be opened: {error}",
    },

    # -- Arac sonuclari (modele giden basari mesajlari) -------------------- #
    "fs.written": {
        "tr": "{path} yazildi ({lines} satir).",
        "en": "{path} written ({lines} lines).",
    },
    "fs.updated": {
        "tr": "{path} guncellendi ({count} degisim).",
        "en": "{path} updated ({count} replacements).",
    },
    "fs.no_glob_match": {
        "tr": "'{pattern}' ile eslesen dosya yok.",
        "en": "No file matches '{pattern}'.",
    },
    "fs.no_grep_match": {
        "tr": "Eslesme yok ({scanned} dosya tarandi).",
        "en": "No matches ({scanned} files scanned).",
    },
    "fs.grep_hits": {
        "tr": "{count} eslesme ({scanned} dosya tarandi):",
        "en": "{count} matches ({scanned} files scanned):",
    },
    "fs.more_files": {
        "tr": "\n…[{count} dosya daha]",
        "en": "\n…[{count} more files]",
    },
    "fs.created_event": {"tr": "olusturuldu", "en": "created"},
    "fs.updated_event": {"tr": "guncellendi", "en": "updated"},
    "fs.edited_event": {
        "tr": "duzenlendi: {path} ({count} eslesme)",
        "en": "edited: {path} ({count} matches)",
    },

    "kb.empty": {
        "tr": "Bilgi tabani bos. Once `ingest_source` ile dokuman ekleyin.",
        "en": "The knowledge base is empty. Add documents with `ingest_source` first.",
    },
    "kb.no_hits": {
        "tr": "'{query}' icin sonuc yok ({chunks} parca tarandi).",
        "en": "No results for '{query}' ({chunks} chunks scanned).",
    },
    "kb.no_more_chunks": {
        "tr": "{title}: {start} sirasindan sonra parca yok.",
        "en": "{title}: no chunks after index {start}.",
    },
    "kb.searched": {
        "tr": "arama: {query} -> {count} parca",
        "en": "search: {query} -> {count} chunks",
    },

    "browser.no_results": {
        "tr": "'{query}' icin {provider} sonuc bulunamadi.",
        "en": "{provider} found no results for '{query}'.",
    },
    "browser.console_clean": {
        "tr": "Sayfa temiz: konsol hatasi, dusen istek ya da 4xx/5xx yanit yok.",
        "en": "The page is clean: no console errors, failed requests or 4xx/5xx responses.",
    },
    "web.searxng_no_json": {
        "tr": "SearXNG ({url}) JSON'u reddetti (403). Ornegin "
              "`settings.yml` dosyasinda `search.formats` listesine `json` "
              "ekleyin; varsayilan olarak kapalidir.",
        "en": "SearXNG ({url}) refused JSON (403). Add `json` to the "
              "`search.formats` list in your instance's `settings.yml`; it is "
              "off by default.",
    },
    "browser.engines_down": {
        "tr": "\n[kapsam daraldi -- cevap vermeyen motorlar: {engines}]",
        "en": "\n[coverage is reduced -- engines that did not respond: {engines}]",
    },
    "web.no_results": {
        "tr": "'{query}' icin sonuc bulunamadi. Farkli terimler deneyin.",
        "en": "No results for '{query}'. Try different terms.",
    },
    "web.search_unavailable": {
        "tr": "'{query}' icin sonuc ALINAMADI.\n"
              "Anahtarsiz DuckDuckGo ucu kendini tanitan istemcileri "
              "engelliyor. Bu bir 'sonuc yok' cevabi DEGILDIR — sorgu "
              "hakkinda hicbir sey ogrenilmedi; boyle bir varsayimda "
              "BULUNMAYIN.\n"
              "Cozum: Ayarlar bolumunden search_provider = brave veya "
              "tavily secip search_api_key girin. Bilinen bir adres "
              "varsa `fetch_url` ile dogrudan okuyabilirsiniz.",
        "en": "Results for '{query}' COULD NOT BE FETCHED.\n"
              "The keyless DuckDuckGo endpoint blocks clients that identify "
              "themselves. This is NOT a 'no results' answer — nothing was "
              "learned about the query; do NOT assume anything from it.\n"
              "The fix: set search_provider = brave or tavily in Settings and "
              "enter a search_api_key. If you know an address, you can read it "
              "directly with `fetch_url`.",
    },

    # -- Kurulum ---------------------------------------------------------- #
    "step.python": {"tr": "Python", "en": "Python"},
    "step.workspace": {"tr": "Calisma alani", "en": "Workspace"},
    "step.dependencies": {"tr": "Bagimliliklar", "en": "Dependencies"},
    "step.docker": {"tr": "Docker", "en": "Docker"},
    "step.searxng": {"tr": "SearXNG", "en": "SearXNG"},
    "step.browser": {"tr": "Tarayici", "en": "Browser"},
    "step.endpoint": {"tr": "Model ucu", "en": "Model endpoint"},
    "step.embedder": {"tr": "Gomme modeli", "en": "Embedding model"},
    "cli.setup": {
        "tr": "Projenin ihtiyaci olan her seyi kurar (bagimlilik, SearXNG, "
              "calisma alani) ve kalanini bildirir.",
        "en": "Installs everything the project needs (dependencies, SearXNG, "
              "the workspace) and reports on the rest.",
    },
    "cli.setup_title": {"tr": "Kurulum", "en": "Setup"},
    "cli.setup_done": {
        "tr": "Kurulum tamam. {installed} kuruldu, {ok} zaten hazirdi.",
        "en": "Setup complete. {installed} installed, {ok} already in place.",
    },
    "cli.setup_blocked": {
        "tr": "{count} sey eksik ve DeerX onlarsiz kosamaz.",
        "en": "{count} things are missing and DeerX cannot run without them.",
    },
    "cli.setup_warned": {
        "tr": "{count} uyari var; DeerX kosar ama o yetenekler kapali kalir.",
        "en": "{count} warnings; DeerX runs but those capabilities stay off.",
    },
    "cli.setup_provider_switched": {
        "tr": "Arama saglayicisi `searxng` yapildi -- kurulan ornek "
              "kullanilmasaydi bosa kurulmus olurdu.",
        "en": "The search provider was switched to `searxng` -- installing the "
              "instance and not using it would have been pointless.",
    },
    "opt.setup_no_deps": {
        "tr": "Bagimliliklari kurma, yalnizca bildir.",
        "en": "Do not install dependencies, only report.",
    },
    "opt.setup_no_searxng": {
        "tr": "SearXNG konteynerini kurma.",
        "en": "Do not install the SearXNG container.",
    },
    "opt.setup_embed_model": {
        "tr": "Gomme modelini simdi indir (~2,2 GB).",
        "en": "Download the embedding model now (~2.2 GB).",
    },
    "col.step": {"tr": "Adim", "en": "Step"},

    "setup.extras_missing": {
        "tr": "eksik ek: {extras}",
        "en": "missing extras: {extras}",
    },
    "setup.extras_failed": {
        "tr": "{extras} kurulamadi; komutu elle calistirin",
        "en": "could not install {extras}; run the command by hand",
    },
    "setup.no_uv": {
        "tr": "`uv` bulunamadi. https://docs.astral.sh/uv/",
        "en": "`uv` not found. https://docs.astral.sh/uv/",
    },
    "setup.browser_absent": {
        "tr": "Chrome bulunamadi. Tarayici araclari (UAT, ekran goruntusu) "
              "kapali kalir; diger fazlar calisir.",
        "en": "Chrome not found. The browser tools (UAT, screenshots) stay "
              "off; the other phases still run.",
    },
    "setup.no_docker": {
        "tr": "Docker bulunamadi. SearXNG kurulamaz; arama icin `brave` ya da "
              "`tavily` anahtari gerekir.",
        "en": "Docker not found. SearXNG cannot be installed; search will need "
              "a `brave` or `tavily` key.",
    },
    "setup.docker_not_running": {
        "tr": "Docker kurulu ama calismiyor.",
        "en": "Docker is installed but not running.",
    },
    "setup.searxng_absent": {
        "tr": "{url} adresinde JSON veren bir ornek yok.",
        "en": "No instance answering JSON at {url}.",
    },
    "setup.searxng_failed": {
        "tr": "Konteyner baslatilamadi: {error}",
        "en": "The container could not be started: {error}",
    },
    "setup.searxng_slow": {
        "tr": "Konteyner basladi ama {url} iki dakikada JSON vermedi.",
        "en": "The container started but {url} did not answer JSON within two "
              "minutes.",
    },
    "setup.endpoint_down": {
        "tr": "{url} yanit vermiyor ({error})",
        "en": "{url} is not responding ({error})",
    },
    "setup.vllm_command": {
        "tr": "docker run --gpus all -p 8008:8000 vllm/vllm-openai:latest "
              "<model> --enable-auto-tool-choice --tool-call-parser <parser>",
        "en": "docker run --gpus all -p 8008:8000 vllm/vllm-openai:latest "
              "<model> --enable-auto-tool-choice --tool-call-parser <parser>",
    },
    "setup.no_workspace": {
        "tr": "Bu dizinde calisma alani yok.",
        "en": "There is no workspace in this directory.",
    },
    "setup.hash_embedder": {
        "tr": "`hash` gomme secili: model indirilmez ama getirme kalitesi "
              "dusuktur.",
        "en": "The `hash` embedder is selected: no download, but retrieval "
              "quality is poor.",
    },
    "setup.model_on_demand": {
        "tr": "{model} ilk indekslemede inecek (~2,2 GB).",
        "en": "{model} will download on first indexing (~2.2 GB).",
    },

    # ── Kayit araclari ────────────────────────────────────────────────── #
    "record.not_an_object": {
        "tr": "{kind} listesinin {index}. ogesi bir nesne degil, {type}: {value}. "
              'Her oge {{"key": "...", "{field}": "..."}} biciminde bir NESNE olmali; '
              "listeye duz metin koymayin.",
        "en": "Item {index} of the {kind} list is not an object but a {type}: {value}. "
              'Every item must be an OBJECT like {{"key": "...", "{field}": "..."}}; '
              "do not put plain strings in the list.",
    },
    "tool.no_state": {
        "tr": "Bu arac icin proje hafizasi baslatilmamis.",
        "en": "Project memory is not initialised for this tool.",
    },
    "tool.unknown": {
        "tr": "'{name}' diye bir arac yok. Kullanilabilir: {names}",
        "en": "There is no tool named '{name}'. Available: {names}",
    },
    "tool.bad_arguments": {
        "tr": "'{name}' icin gecersiz parametreler: {error}",
        "en": "Invalid parameters for '{name}': {error}",
    },
    "record.missing_field": {
        "tr": "{kind} listesinin {index}. kaydinda zorunlu `{field}` alani eksik ya da "
              "bos. O kayitta gonderdiginiz alanlar: {sent}. Eksigi tamamlayip "
              "cagriyi tekrarlayin.",
        "en": "Record {index} of the {kind} list is missing the required `{field}` "
              "field, or it is empty. Fields you sent in that record: {sent}. Fill it "
              "in and repeat the call.",
    },
}
