"""Ekran goruntuleri icin gosterilebilir bir demo calisma alani kurar.

Kullanicinin kendi calisma alanini fotograflamak iki sebeple yanlis olurdu:
icinde onun proje icerigi var ve ekranda Windows kullanici adi geciyor.
Depo herkese acik.

Veri elle kurulur; model cagrilmaz. Amac gercekci bir EKRAN, gercek bir kosu
degil.
"""
from __future__ import annotations

import json
import shutil
import struct
import sys
import time
import zlib
from pathlib import Path

# Depo icinden calistirilir; kurulu bir paket gerekmez.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from deerx.pipeline.models import (  # noqa: E402
    Artifact,
    Decision,
    Gap,
    Question,
    Requirement,
    ResearchNote,
    Task,
)
from deerx.pipeline.state import ProjectState  # noqa: E402
from deerx.web.auth import AuthStore  # noqa: E402

# Demo hesabinin parolasi. GERCEK bir kurulumda kullanilmaz ve kullanilmasi
# gerekmez: bu calisma alani her seferinde sifirdan kurulur, tek isi
# fotograflanmak. `capture.py` bununla giris yapar.
DEMO_PAROLA = "deerx-demo-2026"

# ---------------------------------------------------------------- icerik

EN = {
    "goal": "A field-service platform: work orders, technicians, customer tracking",
    "brief": (
        "Read the spec end to end. Pay attention to the offline story on the "
        "technician's phone and to whatever the existing ERP already owns — "
        "do not re-model data that has a system of record."
    ),
    "requirements": [
        ("REQ-001", "Work-order lifecycle and state transitions", "must", "functional",
         "Created -> assigned -> on site -> completed -> approved. Every "
         "transition is stamped with who and when.", "§2.1"),
        ("REQ-002", "A completion photo is mandatory", "must", "functional",
         "The technician cannot close a work order without at least one photo "
         "and a customer signature.", "§2.4"),
        ("REQ-003", "Offline work on the technician's phone", "must", "nonfunctional",
         "Low-end Android devices, patchy coverage. Queue locally, sync when "
         "the network returns, resolve conflicts last-write-wins per field.", "§5.2"),
        ("REQ-004", "Dispatch by region and skill", "should", "functional",
         "The dispatcher sees who is free, where they are, and what they are "
         "certified for.", "§3.1"),
        ("REQ-005", "SLA breach warning at 80% of the window", "should", "functional",
         "The operations manager is warned before the SLA is missed, not after.",
         "§3.6"),
        ("REQ-006", "Customer sees the status without an account", "should", "functional",
         "A signed link in an SMS. No password, no app install.", "§4.2"),
        ("REQ-007", "99.5% availability during working hours", "must", "nonfunctional",
         "Measured on the dispatch and technician endpoints.", "§6.1"),
        ("REQ-008", "Two-way sync with the existing ERP", "must", "constraint",
         "Stock and invoicing stay in the ERP. This system owns the work order "
         "and nothing else.", "§7.1"),
    ],
    "gaps": [
        ("GAP-001", "The offline conflict rule is not specified", "high", "data model",
         "Two technicians can edit the same work order while both are offline. "
         "The spec does not say who wins.",
         "Last-write-wins per field, with an audit trail; escalate a status "
         "conflict to the dispatcher.",
         "§5.2 describes the queue but not the merge."),
        ("GAP-002", "No retention policy for completion photos", "medium", "operations",
         "Photos are the legal record of the job. Nothing says how long they "
         "are kept or who may read them.",
         "Two years hot, five cold; access limited to the assigned technician "
         "and operations.", "§2.4"),
        ("GAP-003", "SMS delivery failure has no fallback", "medium", "ux",
         "If the SMS never arrives the customer has no way to reach the status.",
         "Fall back to e-mail; surface the failure in the operations panel.",
         "§4.2"),
        ("GAP-004", "The ERP's rate limit is unknown", "high", "integration",
         "A sync loop against an unknown rate limit is the fastest way to be "
         "blocked in production.",
         "Measure it in the sandbox before designing the sync cadence.", "§7.1"),
    ],
    "questions": [
        ("Q-001", "Can you share the ERP's API documentation and a test account?",
         "Without the endpoints and the auth method the sync design is guesswork, "
         "and a wrong guess reaches the architecture, then the plan, then the code.",
         True, "Assume REST + OAuth2 client credentials and revisit."),
        ("Q-002", "How is the SLA window decided — fixed, or by priority and region?",
         "The warning at 80% cannot be built without knowing what 100% is.",
         True, "Fixed 24 hours, overridable per customer."),
        ("Q-003", "Which channel does the customer open a request through?",
         "It decides whether there is a customer-facing form at all.",
         False, "Operator enters it; the customer only tracks."),
        ("Q-004", "What is the minimum Android version on the technicians' phones?",
         "It decides whether the offline store can rely on modern browser APIs.",
         False, "Android 10."),
    ],
    "decisions": [
        ("ADR-001", "Offline store on the phone", "IndexedDB with a write-ahead queue",
         "Survives a reload, works on low-end devices, and needs no native app.",
         "localStorage (too small), native app (a second codebase)",
         "Conflict resolution has to be written by hand."),
        ("ADR-002", "The ERP stays the system of record for stock",
         "One-way read, write only on invoice",
         "Two systems owning the same number is how stock goes wrong.",
         "Full two-way sync", "A stock change is visible with a delay of up to a minute."),
        ("ADR-003", "Customer link", "Signed, expiring URL in an SMS",
         "No account, no password reset, no support load.",
         "Account + password, magic e-mail link",
         "The link leaks if the phone is shared."),
    ],
    "research": [
        ("Offline sync on low-end Android",
         "IndexedDB is available on Android 5+; the practical limit is storage "
         "eviction under pressure, not the API.",
         "https://developer.mozilla.org/docs/Web/API/IndexedDB_API", "high"),
        ("SMS delivery rates in the region",
         "Operator-reported delivery is 94-97%; a fallback channel is standard "
         "practice rather than an optimisation.", "", "medium"),
    ],
    "plan": ("Release 1", "Work orders end to end, technician phone included"),
    "tasks": [
        ("T-001", "Data model and the first migration", "backend", "code", "done",
         ["Work order, technician, customer and the transition log."]),
        ("T-002", "Work-order API and the state machine", "backend", "code", "done", []),
        ("T-003", "Dispatch board", "frontend", "code", "done", []),
        ("T-004", "Technician screen and the offline queue", "frontend", "code", "running", []),
        ("T-005", "Customer tracking page", "frontend", "code", "pending", []),
        ("T-006", "ERP sync worker", "backend", "code", "blocked", []),
        ("T-007", "End-to-end tests for the offline path", "qa", "test", "pending", []),
        ("T-008", "CI, staging environment and secrets", "infra", "infra", "done", []),
        ("T-009", "Operations runbook", "docs", "docs", "pending", []),
    ],
    "phases": {
        "ingest": ("done", "14 files indexed (612 chunks), 1 skipped"),
        "analyze": ("done", "8 requirements, 4 questions — 2 of them blocking"),
        "research": ("done", "2 findings verified on the web, 1 source unreachable"),
        "assess": ("done", "4 gaps: 2 high, 2 medium"),
        "mockup": ("done", "4 mockups: dispatch board, technician list, work order, customer page"),
        "design": ("done", "3 architecture decisions recorded, data model drawn"),
        "plan": ("done", "9 tasks across 5 lanes"),
        "implement": ("running", "4 tasks done, 1 running, 1 blocked"),
    },
    "events": [
        ("phase", "pipeline", "Implementation started"),
        ("agent", "backend", "T-004 · Technician screen and the offline queue"),
        ("tool", "shell", "npm run dev"),
        ("tool", "browser", "opened http://127.0.0.1:5173/technician"),
        ("tool", "browser", "screenshot: technician-list.png"),
        ("warn", "qa", "console error: Uncaught TypeError at queue.js:88"),
        ("tool", "files", "wrote src/offline/queue.js (2.1 KB)"),
        ("tool", "shell", "npm test -- offline"),
        ("done", "qa", "18 tests pass, 0 fail"),
        ("cost", "run", "$0.0000 · local model"),
    ],
}

TR = {
    "goal": "Saha servis platformu: iş emri, teknisyen, müşteri takibi",
    "brief": (
        "Şartnameyi baştan sona okuyun. Teknisyenin telefonundaki çevrimdışı "
        "senaryoya ve mevcut ERP'nin zaten sahiplendiği verilere dikkat edin — "
        "kayıt sistemi olan bir veriyi yeniden modellemeyin."
    ),
    "requirements": [
        ("REQ-001", "İş emri yaşam döngüsü ve geçiş kuralları", "must", "functional",
         "Açıldı → atandı → sahada → tamamlandı → onaylandı. Her geçiş kimin, "
         "ne zaman yaptığıyla damgalanır.", "§2.1"),
        ("REQ-002", "Tamamlama için fotoğraf zorunlu", "must", "functional",
         "Teknisyen en az bir fotoğraf ve müşteri imzası olmadan iş emrini "
         "kapatamaz.", "§2.4"),
        ("REQ-003", "Teknisyen telefonunda çevrimdışı çalışma", "must", "nonfunctional",
         "Düşük donanımlı Android, kesintili kapsama. Yerelde kuyruğa alınır, "
         "ağ dönünce eşitlenir.", "§5.2"),
        ("REQ-004", "Bölge ve yetkinliğe göre iş atama", "should", "functional",
         "Operatör kimin boşta olduğunu, nerede olduğunu ve neye sertifikalı "
         "olduğunu görür.", "§3.1"),
        ("REQ-005", "SLA süresinin %80'inde uyarı", "should", "functional",
         "Operasyon yöneticisi SLA kaçırılmadan önce uyarılır, sonra değil.",
         "§3.6"),
        ("REQ-006", "Müşteri hesapsız durum görebilsin", "should", "functional",
         "SMS içinde imzalı bağlantı. Parola yok, uygulama kurulumu yok.", "§4.2"),
        ("REQ-007", "Çalışma saatlerinde %99,5 erişilebilirlik", "must", "nonfunctional",
         "Atama ve teknisyen uçlarında ölçülür.", "§6.1"),
        ("REQ-008", "Mevcut ERP ile çift yönlü eşitleme", "must", "constraint",
         "Stok ve faturalama ERP'de kalır. Bu sistem yalnızca iş emrinin "
         "sahibidir.", "§7.1"),
    ],
    "gaps": [
        ("GAP-001", "Çevrimdışı çakışma kuralı yazılmamış", "high", "veri modeli",
         "İki teknisyen aynı iş emrini çevrimdışıyken düzenleyebilir; "
         "şartname kimin kazandığını söylemiyor.",
         "Alan bazında son yazan kazanır, denetim kaydıyla; durum çakışması "
         "operatöre yükseltilir.", "§5.2 kuyruğu anlatıyor, birleştirmeyi değil."),
        ("GAP-002", "Tamamlama fotoğrafları için saklama süresi yok", "medium", "operasyon",
         "Fotoğraf işin hukuki kaydı. Ne kadar saklanacağı ve kimin "
         "okuyabileceği yazmıyor.",
         "İki yıl sıcak, beş yıl soğuk; erişim atanan teknisyen ve operasyonla "
         "sınırlı.", "§2.4"),
        ("GAP-003", "SMS ulaşmazsa alternatif yok", "medium", "kullanıcı deneyimi",
         "SMS hiç ulaşmazsa müşterinin duruma erişmesinin başka yolu yok.",
         "E-postaya düşülür; başarısızlık operasyon panelinde görünür.", "§4.2"),
        ("GAP-004", "ERP'nin hız sınırı bilinmiyor", "high", "entegrasyon",
         "Bilinmeyen bir hız sınırına karşı eşitleme döngüsü kurmak, "
         "canlıda engellenmenin en hızlı yolu.",
         "Tasarımdan önce test ortamında ölçülmeli.", "§7.1"),
    ],
    "questions": [
        ("Q-001", "Mevcut ERP'nin API dokümanı ve test erişimi paylaşılabilir mi?",
         "Uçlar ve kimlik doğrulama yöntemi bilinmeden eşitleme tasarımı "
         "tahmine dayanır; yanlış tahmin mimariye, oradan plana ve koda sızar.",
         True, "REST + OAuth2 client credentials varsayıp sonra dönelim."),
        ("Q-002", "SLA süresi nasıl belirleniyor — sabit mi, önceliğe göre mi?",
         "%80'de uyarı, %100'ün ne olduğu bilinmeden kurulamaz.",
         True, "Sabit 24 saat, müşteri bazında değiştirilebilir."),
        ("Q-003", "Müşteri talebi hangi kanaldan açıyor?",
         "Müşteriye açık bir form olup olmayacağını belirler.",
         False, "Operatör giriyor; müşteri yalnızca takip ediyor."),
        ("Q-004", "Teknisyen telefonlarındaki asgari Android sürümü nedir?",
         "Çevrimdışı deponun modern tarayıcı API'lerine güvenip "
         "güvenemeyeceğini belirler.", False, "Android 10."),
    ],
    "decisions": [
        ("ADR-001", "Telefondaki çevrimdışı depo", "Yazma kuyruklu IndexedDB",
         "Yenilemeye dayanır, düşük donanımda çalışır, native uygulama "
         "gerektirmez.", "localStorage (çok küçük), native uygulama (ikinci kod tabanı)",
         "Çakışma çözümü elle yazılmak zorunda."),
        ("ADR-002", "Stokun kayıt sistemi ERP'de kalır",
         "Tek yönlü okuma, yalnızca faturada yazma",
         "Aynı sayının iki sahibi olması, stokun bozulma şeklidir.",
         "Tam çift yönlü eşitleme", "Stok değişikliği bir dakikaya kadar gecikmeyle görünür."),
        ("ADR-003", "Müşteri bağlantısı", "SMS içinde imzalı, süreli URL",
         "Hesap yok, parola sıfırlama yok, destek yükü yok.",
         "Hesap + parola, e-posta bağlantısı",
         "Telefon paylaşılıyorsa bağlantı sızar."),
    ],
    "research": [
        ("Düşük donanımlı Android'de çevrimdışı eşitleme",
         "IndexedDB Android 5+ ile geliyor; pratikteki sınır API değil, "
         "baskı altında depolama tahliyesi.",
         "https://developer.mozilla.org/docs/Web/API/IndexedDB_API", "high"),
        ("Bölgedeki SMS teslim oranları",
         "Operatörlerin bildirdiği teslim %94-97; alternatif kanal bir "
         "iyileştirme değil, standart uygulama.", "", "medium"),
    ],
    "plan": ("Sürüm 1", "İş emri uçtan uca, teknisyen telefonu dahil"),
    "tasks": [
        ("T-001", "Veri modeli ve ilk göç", "backend", "code", "done", []),
        ("T-002", "İş emri API'si ve durum makinesi", "backend", "code", "done", []),
        ("T-003", "Atama panosu", "frontend", "code", "done", []),
        ("T-004", "Teknisyen ekranı ve çevrimdışı kuyruk", "frontend", "code", "running", []),
        ("T-005", "Müşteri takip sayfası", "frontend", "code", "pending", []),
        ("T-006", "ERP eşitleme işçisi", "backend", "code", "blocked", []),
        ("T-007", "Çevrimdışı yol için uçtan uca testler", "qa", "test", "pending", []),
        ("T-008", "CI, staging ortamı ve sırlar", "infra", "infra", "done", []),
        ("T-009", "Operasyon el kitabı", "docs", "docs", "pending", []),
    ],
    "phases": {
        "ingest": ("done", "14 dosya indekslendi (612 parça), 1 atlandı"),
        "analyze": ("done", "8 gereksinim, 4 soru — ikisi engelleyici"),
        "research": ("done", "2 bulgu webde doğrulandı, 1 kaynak açılmadı"),
        "assess": ("done", "4 boşluk: 2 yüksek, 2 orta"),
        "mockup": ("done", "4 mockup: atama panosu, teknisyen listesi, iş emri, müşteri sayfası"),
        "design": ("done", "3 mimari karar kaydedildi, veri modeli çizildi"),
        "plan": ("done", "5 şeritte 9 görev"),
        "implement": ("running", "4 görev tamam, 1 çalışıyor, 1 bloke"),
    },
    "events": [
        ("phase", "boru hatti", "Uygulama basladi"),
        ("agent", "backend", "T-004 · Teknisyen ekrani ve cevrimdisi kuyruk"),
        ("tool", "shell", "npm run dev"),
        ("tool", "browser", "acildi: http://127.0.0.1:5173/teknisyen"),
        ("tool", "browser", "ekran goruntusu: teknisyen-liste.png"),
        ("warn", "qa", "konsol hatasi: Uncaught TypeError at queue.js:88"),
        ("tool", "files", "yazildi: src/offline/queue.js (2.1 KB)"),
        ("tool", "shell", "npm test -- offline"),
        ("done", "qa", "18 test gecti, 0 hata"),
        ("cost", "run", "$0.0000 · yerel model"),
    ],
}


# --------------------------------------------------------------- yardimci

def _png(path: Path, width: int, height: int) -> None:
    """Kucuk bir degrade PNG; ekran goruntusu ciktisini temsil eder."""
    def parca(tur: bytes, veri: bytes) -> bytes:
        return (struct.pack(">I", len(veri)) + tur + veri
                + struct.pack(">I", zlib.crc32(tur + veri) & 0xFFFFFFFF))

    satirlar = bytearray()
    for y in range(height):
        satirlar.append(0)
        for x in range(width):
            satirlar += bytes([
                40 + (x * 60) // width,
                60 + (y * 90) // height,
                110 + (x * 90) // width,
            ])
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + parca(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + parca(b"IDAT", zlib.compress(bytes(satirlar), 9))
        + parca(b"IEND", b"")
    )


MOCKUP = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Dispatch board</title>
<style>
  :root { color-scheme: light; }
  body { margin:0; font:14px/1.5 system-ui,sans-serif; background:#f5f6f8; color:#1a1d23; }
  header { display:flex; align-items:center; justify-content:space-between;
           padding:14px 22px; background:#fff; border-bottom:1px solid #e3e6ea; }
  h1 { font-size:16px; margin:0; }
  .pill { font-size:12px; padding:3px 10px; border-radius:99px; background:#eef2ff; color:#3b4ea8; }
  main { display:grid; grid-template-columns:260px 1fr; gap:18px; padding:18px 22px; }
  .card { background:#fff; border:1px solid #e3e6ea; border-radius:10px; padding:14px 16px; }
  .card h2 { font-size:12px; text-transform:uppercase; letter-spacing:.06em;
             color:#6b7280; margin:0 0 10px; }
  .tech { display:flex; align-items:center; gap:10px; padding:8px 0;
          border-top:1px solid #f0f1f4; }
  .tech:first-of-type { border-top:0; }
  .dot { width:8px; height:8px; border-radius:99px; background:#22c55e; }
  .dot.busy { background:#f59e0b; }
  table { width:100%; border-collapse:collapse; }
  th { text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.06em;
       color:#6b7280; padding:0 0 8px; }
  td { padding:9px 0; border-top:1px solid #f0f1f4; }
  .tag { font-size:11px; padding:2px 8px; border-radius:5px; background:#f1f5f9; color:#475569; }
  .tag.late { background:#fee2e2; color:#b91c1c; }
  .tag.ok { background:#dcfce7; color:#15803d; }
</style></head><body>
<header><h1>Dispatch board</h1><span class="pill">12 open · 3 at risk</span></header>
<main>
  <section class="card">
    <h2>Technicians</h2>
    <div class="tech"><span class="dot"></span><span>A. Yilmaz · North</span></div>
    <div class="tech"><span class="dot busy"></span><span>M. Kaya · Centre</span></div>
    <div class="tech"><span class="dot"></span><span>S. Demir · South</span></div>
    <div class="tech"><span class="dot busy"></span><span>E. Ozturk · East</span></div>
  </section>
  <section class="card">
    <h2>Work orders</h2>
    <table>
      <thead><tr><th>No</th><th>Customer</th><th>Region</th><th>SLA</th><th>State</th></tr></thead>
      <tbody>
        <tr><td>#4821</td><td>Aker Textile</td><td>North</td>
            <td><span class="tag late">2 h left</span></td><td>on site</td></tr>
        <tr><td>#4822</td><td>Bolu Cold Storage</td><td>Centre</td>
            <td><span class="tag">9 h left</span></td><td>assigned</td></tr>
        <tr><td>#4823</td><td>Deniz Market</td><td>South</td>
            <td><span class="tag ok">completed</span></td><td>awaiting approval</td></tr>
        <tr><td>#4824</td><td>Ege Plastics</td><td>East</td>
            <td><span class="tag">17 h left</span></td><td>created</td></tr>
      </tbody>
    </table>
  </section>
</main></body></html>
"""


def denetim(db: Path, icerik: dict) -> None:
    """Kullanicilar ve gosterilebilir bir denetim gunlugu.

    Ekip, reddedilen giris denemeleri ve silinmis bir hesap: panelin ne ise
    yaradigi ancak boyle gorunur. Bos bir gunlugun fotografi, ozelligin
    olmadigini dusundurur.
    """
    store = AuthStore(db)
    yonetici = store.create_first_admin(
        store.issue_setup_token(), "deniz", DEMO_PAROLA, display_name="Deniz"
    )
    mert = store.create_user("mert", DEMO_PAROLA, display_name="Mert")
    ayrilan = store.create_user("elif", DEMO_PAROLA, display_name="Elif")

    # Sira ONEMLI: gunluk en yeniden eskiye dogru okunur ve kadraja ilk
    # ekran dolusu girer. Reddedilen denemeler listenin dibinde kalirsa
    # fotografta gorunmez -- oysa yoneticinin bakacagi ilk satirlar onlar.
    gorev = icerik["tasks"][3]          # T-004, "running"
    kayitlar = [
        ("login", yonetici, None, "", "", {}, "192.168.1.14", "Firefox/142", True),
        ("user.create", yonetici, None, "mert · user", "", {},
         "192.168.1.14", "Firefox/142", True),
        ("login", mert, None, "", "", {}, "192.168.1.31", "Chrome/141", True),
        ("knowledge.upload", mert, None, "field-service-spec-v3.pdf", "", {},
         "192.168.1.31", "Chrome/141", True),
        ("run.start", mert, None, "", "runs.titlePhase", {"phase": "analyze"},
         "192.168.1.31", "Chrome/141", True),
        ("login", ayrilan, None, "", "", {}, "192.168.1.77", "Safari/18", True),
        ("settings.change", yonetici, None, "max_tokens, model_lead", "", {},
         "192.168.1.14", "Firefox/142", True),
        ("run.start", mert, None, "", "runs.titleTask",
         {"key": gorev[0], "title": gorev[1]}, "192.168.1.31", "Chrome/141", True),
        ("login.failed", None, "admin", "", "", {}, "203.0.113.9", "curl/8.5", False),
        ("login.failed", None, "admin", "", "", {}, "203.0.113.9", "curl/8.5", False),
        ("login.failed", None, "root", "", "", {}, "203.0.113.9", "curl/8.5", False),
        ("run.stop", yonetici, None, "", "", {}, "192.168.1.14", "Firefox/142", True),
        ("package.build", yonetici, None, "deerx-delivery-0007.zip", "", {},
         "192.168.1.14", "Firefox/142", True),
        ("logout", mert, None, "", "", {}, "192.168.1.31", "Chrome/141", True),
    ]
    for eylem, kisi, ad, ayrinti, anahtar, args, ip, tarayici, tamam in kayitlar:
        store.record(eylem, user=kisi, username=ad, detail=ayrinti,
                     detail_key=anahtar, detail_args=args, ip=ip,
                     agent=tarayici, ok=tamam)

    # Ayrilan biri: satirlari kalir, hesabi kalmaz.
    store.delete_user(ayrilan.id)

    # Zamanlari birkac gune yay; hepsi ayni saniyede olmus gibi durmasin.
    simdi = time.time()
    kimlikler = [r["id"] for r in store.list_audit(limit=100)][::-1]
    for sira, kimlik in enumerate(kimlikler):
        store._conn.execute(
            "UPDATE audit SET at = ? WHERE id = ?",
            (simdi - (len(kimlikler) - sira) * 7300, kimlik),
        )
    store._conn.commit()
    store.close()


def kur(kok: Path, icerik: dict, dil: str) -> None:
    if kok.exists():
        shutil.rmtree(kok)
    veri = kok / ".deerx"
    (veri / "artifacts").mkdir(parents=True)

    st = ProjectState(veri / "deerx.db")
    st.set_meta("goal", icerik["goal"])
    st.set_meta("brief", icerik["brief"])

    for key, title, pri, cat, desc, ref in icerik["requirements"]:
        st.add_requirement(Requirement(key=key, title=title, description=desc,
                                       category=cat, priority=pri, source_ref=ref))
    for key, title, sev, area, desc, rec, ev in icerik["gaps"]:
        st.add_gap(Gap(key=key, title=title, description=desc, severity=sev,
                       area=area, recommendation=rec, evidence=ev))
    for key, q, why, blocking, sug in icerik["questions"]:
        st.add_question(Question(key=key, question=q, why=why,
                                 blocking=blocking, suggestion=sug))
    for key, title, choice, rat, alt, tra in icerik["decisions"]:
        st.add_decision(Decision(key=key, title=title, choice=choice,
                                 rationale=rat, alternatives=alt, tradeoffs=tra))
    for topic, finding, url, conf in icerik["research"]:
        st.add_research_note(ResearchNote(topic=topic, finding=finding,
                                          url=url, confidence=conf))

    ad, aciklama = icerik["plan"]
    plan = st.create_plan(ad, description=aciklama)
    for i, (key, title, lane, kind, status, _n) in enumerate(icerik["tasks"]):
        st.add_task(
            Task(key=key, title=title, lane=lane, kind=kind, status=status,
                 deps=[icerik["tasks"][i - 1][0]] if i and lane == "frontend" else [],
                 order_index=i),
            plan_id=plan["id"],
        )

    for faz, (durum, ozet) in icerik["phases"].items():
        if durum == "running":
            st.start_phase(faz)
        else:
            st.finish_phase(faz, status=durum, summary=ozet, cost_usd=0.0)

    # Is akisi + kosular
    wf = st.create_workflow(icerik["goal"], brief=icerik["brief"])
    kosular = [
        ("a1b2c3d4e5f6", ["ingest", "analyze", "research", "assess",
                          "mockup", "design", "plan"],
         "runs.titlePhases", {"first": "ingest", "last": "plan"}, "done"),
        ("b2c3d4e5f6a1", ["implement"], "runs.titleTask",
         {"key": "T-003", "title": icerik["tasks"][2][1]}, "done"),
        ("c3d4e5f6a1b2", ["implement"], "runs.titleTask",
         {"key": "T-004", "title": icerik["tasks"][3][1]}, "running"),
    ]
    simdi = time.time()
    for i, (rid, fazlar, tk, ta, durum) in enumerate(kosular):
        st.start_run(rid, goal=icerik["goal"], brief=icerik["brief"],
                     phases=fazlar, workflow_id=wf["id"],
                     title=" → ".join(fazlar), title_key=tk, title_args=ta)
        for j, faz in enumerate(fazlar):
            st.start_run_step(rid, faz, j)
            if durum == "done" or j < len(fazlar) - 1:
                st.finish_run_step(rid, faz, status="done",
                                   summary=icerik["phases"].get(faz, ("", ""))[1])
        if durum == "done":
            st.finish_run(rid, status="done")
        st._conn.execute(  # noqa: SLF001 - gosterim icin gecmis zaman damgasi
            "UPDATE runs SET started_at=?, finished_at=? WHERE id=?",
            (simdi - (3 - i) * 2400, simdi - (3 - i) * 2400 + 900 if durum == "done" else None, rid),
        )
        st._conn.commit()  # noqa: SLF001

    # Ciktilar
    art = veri / "artifacts"
    (art / "dispatch-board.html").write_text(MOCKUP, encoding="utf-8")
    _png(art / "technician-list.png", 960, 540)
    (art / "architecture.md").write_text(
        "# Architecture\n\n## Data model\n\nThe work order is the aggregate root; "
        "the transition log hangs off it and is append-only.\n\n"
        "## Offline\n\nIndexedDB plus a write-ahead queue on the phone; the "
        "server is the arbiter on sync.\n", encoding="utf-8")
    for name, kind, phase, summary, rid in (
        ("dispatch-board.html", "mockup", "mockup",
         "Single-file HTML, no external dependency", "a1b2c3d4e5f6"),
        ("architecture.md", "architecture", "design",
         "Data model and the offline story", "a1b2c3d4e5f6"),
        ("technician-list.png", "screenshot", "implement",
         "http://127.0.0.1:5173/technician", "c3d4e5f6a1b2"),
    ):
        st.add_artifact(Artifact(name=name, kind=kind, path=str(art / name),
                                 summary=summary, run_id=rid, phase=phase))
    st.close()

    denetim(veri / "deerx.db", icerik)

    # Olay gunlugu
    with (veri / "events.jsonl").open("w", encoding="utf-8") as fh:
        for i, (kind, actor, message) in enumerate(icerik["events"]):
            fh.write(json.dumps(
                {"kind": kind, "actor": actor, "message": message,
                 "ts": simdi - (len(icerik["events"]) - i) * 37,
                 "data": {}, "phase": "implement", "run_id": "c3d4e5f6a1b2"},
                ensure_ascii=False) + "\n")

    print(f"  {dil}: {kok}")


if __name__ == "__main__":
    temel = Path(sys.argv[1])
    kur(temel / "demo-en", EN, "en")
    kur(temel / "demo-tr", TR, "tr")
