"""Demo calisma alanina birkac belge indeksler.

Genel bakisin "DOKUMAN 0" demesi, ekran goruntusunde boru hattinin hic
calismamis oldugu izlenimini veriyordu. Gomme saglayicisi `hash`: model
indirilmez, ag kullanilmaz -- gosterim icin yeterli.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Depo icinden calistirilir; kurulu bir paket gerekmez.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from deerx.config import Settings  # noqa: E402
from deerx.rag.knowledge import KnowledgeBase  # noqa: E402

BELGELER_EN = {
    "spec/work-orders.md": """# Work orders

A work order moves through created, assigned, on site, completed and
approved. Every transition records who made it and when; the log is
append-only because it is the evidence of what happened on site.

Completion requires at least one photo and a customer signature. A
technician cannot close a job without both.
""",
    "spec/technician-app.md": """# The technician's phone

Technicians carry low-end Android devices and work in buildings with
patchy coverage. Everything they do is queued locally and synced when the
network returns.

The queue survives a reload. A job started offline and finished offline
must reach the server intact.
""",
    "spec/integration.md": """# ERP integration

Stock and invoicing stay in the existing ERP. This system owns the work
order and nothing else; a number with two owners is a number that goes
wrong.

Sync is one-way read, with a write only when an invoice is raised.
""",
    "spec/sla.md": """# Service levels

Availability is 99.5% during working hours, measured on the dispatch and
technician endpoints. The operations manager is warned at 80% of the SLA
window, before it is missed rather than after.
""",
    "notes/kickoff.md": """# Kick-off notes

The customer must be able to see the state of their job without an
account. A signed link in an SMS, no password, no app install.

Open question from the room: which channel do customers use to open a
request in the first place?
""",
}

BELGELER_TR = {
    "sartname/is-emri.md": """# İş emirleri

Bir iş emri açıldı, atandı, sahada, tamamlandı ve onaylandı durumlarından
geçer. Her geçiş kimin, ne zaman yaptığını kaydeder; kayıt yalnızca
eklenir, çünkü sahada ne olduğunun kanıtı odur.

Tamamlama en az bir fotoğraf ve müşteri imzası ister. Teknisyen ikisi
olmadan işi kapatamaz.
""",
    "sartname/teknisyen-uygulamasi.md": """# Teknisyenin telefonu

Teknisyenler düşük donanımlı Android cihaz taşıyor ve kapsamanın zayıf
olduğu binalarda çalışıyor. Yaptıkları her şey yerelde kuyruğa alınır ve
ağ döndüğünde eşitlenir.

Kuyruk sayfa yenilemesine dayanır. Çevrimdışı başlayıp çevrimdışı biten
bir iş sunucuya eksiksiz ulaşmalıdır.
""",
    "sartname/entegrasyon.md": """# ERP entegrasyonu

Stok ve faturalama mevcut ERP'de kalır. Bu sistem yalnızca iş emrinin
sahibidir; iki sahibi olan bir sayı, bozulan bir sayıdır.

Eşitleme tek yönlü okumadır; yazma yalnızca fatura kesilirken olur.
""",
    "sartname/hizmet-seviyesi.md": """# Hizmet seviyeleri

Çalışma saatlerinde erişilebilirlik %99,5; atama ve teknisyen uçlarında
ölçülür. Operasyon yöneticisi SLA süresinin %80'inde, kaçırıldıktan sonra
değil önce uyarılır.
""",
    "notlar/acilis.md": """# Açılış toplantısı notları

Müşteri işinin durumunu hesap açmadan görebilmeli. SMS içinde imzalı bir
bağlantı; parola yok, uygulama kurulumu yok.

Toplantıda açık kalan soru: müşteri talebi ilk olarak hangi kanaldan
açıyor?
""",
}


def kur(kok: Path, belgeler: dict[str, str]) -> None:
    ayar = Settings(workspace=kok)
    ayar.rag.embedding_provider = "hash"
    ayar.rag.embedding_dim = 128
    ayar.ensure_dirs()

    for goreli, metin in belgeler.items():
        yol = kok / goreli
        yol.parent.mkdir(parents=True, exist_ok=True)
        yol.write_text(metin, encoding="utf-8")

    kb = KnowledgeBase(ayar)
    print(f"    gomme: {kb.embedder.name} / {kb.embedder.dim}")
    toplam = 0
    for goreli in belgeler:
        sonuc = kb.ingest_file(kok / goreli)
        toplam += sonuc.chunks
    print(f"  {kok.name}: {len(belgeler)} belge, {toplam} parca")
    kb.close()


if __name__ == "__main__":
    temel = Path(sys.argv[1])
    kur(temel / "demo-en", BELGELER_EN)
    kur(temel / "demo-tr", BELGELER_TR)
