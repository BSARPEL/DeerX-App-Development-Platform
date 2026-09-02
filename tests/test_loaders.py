"""Dokuman yukleyici testleri — gercek PDF/DOCX/HTML dosyalariyla."""

from __future__ import annotations

from pathlib import Path

import pytest

from deerx.errors import IngestError
from deerx.rag.loaders import (
    classify,
    is_supported,
    iter_files,
    load_file,
    load_html_text,
)


def build_pdf(lines: list[str]) -> bytes:
    """Metin cikarilabilir, elle uretilmis minimal bir PDF.

    Harici bir PDF yazma bagimliligi eklemek yerine bicimin kendisini uretiriz;
    boylece test, pypdf'in gercekten metni cikarabildigini dogrular.
    """
    ops = (
        "BT /F1 12 Tf 72 720 Td 14 TL\n"
        + "".join(f"({line}) Tj T*\n" for line in lines)
        + "ET"
    )
    stream = ops.encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


class TestClassification:
    @pytest.mark.parametrize(
        ("name", "kind"),
        [
            ("a.md", "doc"), ("a.pdf", "doc"), ("a.docx", "doc"), ("a.txt", "doc"),
            ("a.py", "code"), ("a.ts", "code"), ("a.go", "code"), ("a.sql", "code"),
            ("a.json", "data"), ("a.yaml", "data"), ("a.toml", "data"),
            ("a.html", "web"),
        ],
    )
    def test_kind(self, name, kind):
        assert classify(Path(name)) == kind

    def test_unsupported_suffix(self):
        assert not is_supported(Path("a.exe"))
        assert is_supported(Path("a.md"))


class TestTextFormats:
    def test_markdown(self, tmp_path: Path):
        path = tmp_path / "a.md"
        path.write_text("# Baslik\n\nGovde metni.", encoding="utf-8")
        doc = load_file(path)
        assert doc.kind == "doc" and "Baslik" in doc.text
        assert doc.sha256

    def test_turkish_characters_survive(self, tmp_path: Path):
        path = tmp_path / "tr.md"
        path.write_text("# Çalışma\n\nİş emri güncellendi. Şoför öğle.", encoding="utf-8")
        assert "Çalışma" in load_file(path).text
        assert "öğle" in load_file(path).text

    def test_cp1254_fallback(self, tmp_path: Path):
        path = tmp_path / "eski.txt"
        path.write_bytes("Türkçe içerik".encode("cp1254"))
        assert "içerik" in load_file(path).text

    def test_json_is_pretty_printed(self, tmp_path: Path):
        path = tmp_path / "a.json"
        path.write_text('{"b":2,"a":[1,2]}', encoding="utf-8")
        text = load_file(path).text
        assert "\n" in text  # tek satirdan cok satira acildi
        assert load_file(path).kind == "data"

    def test_malformed_json_is_kept_raw(self, tmp_path: Path):
        path = tmp_path / "a.json"
        path.write_text("{bozuk", encoding="utf-8")
        assert load_file(path).text == "{bozuk"

    def test_empty_file_rejected(self, tmp_path: Path):
        path = tmp_path / "bos.md"
        path.write_text("   \n", encoding="utf-8")
        with pytest.raises(IngestError, match="bos"):
            load_file(path)

    def test_missing_file(self, tmp_path: Path):
        with pytest.raises(IngestError, match="bulunamadi"):
            load_file(tmp_path / "yok.md")

    def test_size_limit(self, tmp_path: Path):
        path = tmp_path / "buyuk.txt"
        path.write_text("x" * 5000, encoding="utf-8")
        with pytest.raises(IngestError, match="cok buyuk"):
            load_file(path, max_bytes=1000)


class TestPdf:
    def test_text_extraction_with_page_markers(self, tmp_path: Path):
        path = tmp_path / "sartname.pdf"
        path.write_bytes(build_pdf(["Sartname Basligi", "Cevrimdisi calisma sart", "KVKK uyumu"]))
        doc = load_file(path)
        assert doc.kind == "doc"
        assert doc.meta["pages"] == 1
        assert "[sayfa 1]" in doc.text  # alintida sayfa gosterebilmek icin
        assert "Cevrimdisi calisma sart" in doc.text

    def test_corrupt_pdf_becomes_ingest_error(self, tmp_path: Path):
        """Bozuk tek bir dosya butun indekslemeyi dusurmemeli."""
        path = tmp_path / "bozuk.pdf"
        path.write_bytes(b"bu bir PDF degil")
        with pytest.raises(IngestError, match="PDF"):
            load_file(path)

    def test_corrupt_docx_becomes_ingest_error(self, tmp_path: Path):
        pytest.importorskip("docx")
        path = tmp_path / "bozuk.docx"
        path.write_bytes(b"PK bozuk icerik")
        with pytest.raises(IngestError, match="DOCX"):
            load_file(path)

    def test_directory_scan_survives_a_broken_file(self, kb, workspace):
        """Bozuk dosya raporlanir, saglam dosyalar yine de indekslenir."""
        (workspace / "docs" / "bozuk.pdf").write_bytes(b"bu bir PDF degil")
        results = kb.ingest_path(workspace / "docs")
        ok = [r for r in results if r.ok]
        failed = [r for r in results if not r.ok]
        assert len(ok) == 1 and ok[0].title == "sartname.md"
        assert len(failed) == 1 and "bozuk.pdf" in failed[0].title
        assert kb.stats()["chunks"] > 0

    def test_textless_pdf_gives_actionable_error(self, tmp_path: Path):
        path = tmp_path / "taranmis.pdf"
        path.write_bytes(build_pdf([]))
        with pytest.raises(IngestError, match="OCR"):
            load_file(path)


class TestDocx:
    def test_headings_become_markdown(self, tmp_path: Path):
        docx = pytest.importorskip("docx")
        document = docx.Document()
        document.add_heading("Ana Baslik", level=1)
        document.add_paragraph("Giris paragrafi.")
        document.add_heading("Alt Baslik", level=2)
        document.add_paragraph("Detay metni.")
        table = document.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "Alan"
        table.cell(0, 1).text = "Deger"
        table.cell(1, 0).text = "SLA"
        table.cell(1, 1).text = "4 saat"

        path = tmp_path / "sartname.docx"
        document.save(str(path))

        doc = load_file(path)
        # Baslik hiyerarsisi markdown'a cevrilmeli ki parcalayici yapiyi gorsun.
        assert "# Ana Baslik" in doc.text
        assert "## Alt Baslik" in doc.text
        assert "Detay metni." in doc.text
        assert "SLA | 4 saat" in doc.text

    def test_docx_chunking_uses_converted_headings(self, tmp_path: Path):
        docx = pytest.importorskip("docx")
        from deerx.rag.chunker import chunk_text

        document = docx.Document()
        document.add_heading("Gereksinimler", level=1)
        document.add_paragraph("R1 aciklamasi.")
        document.add_heading("Kisitlar", level=1)
        document.add_paragraph("K1 aciklamasi.")
        path = tmp_path / "a.docx"
        document.save(str(path))

        chunks = chunk_text(load_file(path).text, kind="doc", max_tokens=40, min_tokens=1)
        assert any("Gereksinimler" in c.heading_path for c in chunks)
        assert any("Kisitlar" in c.heading_path for c in chunks)


class TestHtml:
    def test_scripts_and_nav_are_stripped(self):
        html = """
        <html><head><style>p{color:red}</style></head>
        <body><nav>Menu baglantilari</nav>
        <h1>Urun Dokumani</h1><p>Ana icerik burada.</p>
        <script>console.log('gurultu')</script>
        <footer>Alt bilgi</footer></body></html>
        """
        doc = load_html_text(html, source="https://ornek.test/doc")
        assert "Ana icerik burada." in doc.text
        assert "gurultu" not in doc.text
        assert "Menu baglantilari" not in doc.text
        assert doc.kind == "web"

    def test_headings_become_markdown(self):
        doc = load_html_text("<h2>Baslik</h2><p>Govde</p>", source="x")
        assert "## Baslik" in doc.text

    def test_empty_html_rejected(self):
        with pytest.raises(IngestError):
            load_html_text("<html><body><script>x=1</script></body></html>", source="x")


class TestIterFiles:
    def test_include_and_exclude(self, tmp_path: Path):
        (tmp_path / "src").mkdir()
        (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
        (tmp_path / "src" / "a.py").write_text("x", encoding="utf-8")
        (tmp_path / "src" / "b.md").write_text("x", encoding="utf-8")
        (tmp_path / "node_modules" / "pkg" / "c.py").write_text("x", encoding="utf-8")

        found = iter_files(tmp_path, ["**/*.py", "**/*.md"], ["**/node_modules/**"])
        names = {p.name for p in found}
        assert names == {"a.py", "b.md"}

    def test_no_duplicates_across_patterns(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x", encoding="utf-8")
        found = iter_files(tmp_path, ["**/*.py", "*.py", "**/*"], [])
        assert len([p for p in found if p.name == "a.py"]) == 1
