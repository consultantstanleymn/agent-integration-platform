import io

from pypdf import PdfReader

from aip.providers.fake import FakeProvider
from aip.rag.chunking import chunk_text
from aip.rag.extract import extract_text
from aip.rag.service import ingest_document, retrieve
from aip.tenants import create_tenant


def make_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)
    return out


def test_chunking_keeps_heading_context():
    chunks = chunk_text("# Refunds\n\nWithin 30 days.\n\n# Shipping\n\nTakes 3 days.")
    assert chunks == ["# Refunds\nWithin 30 days.", "# Shipping\nTakes 3 days."]


def test_chunking_splits_long_text_and_respects_size():
    chunks = chunk_text("word " * 400, max_chars=300, overlap=30)
    assert len(chunks) > 3
    assert all(len(c) <= 300 for c in chunks)


def test_chunking_empty():
    assert chunk_text("  \n\n ") == []


def test_extract_text_md_txt_and_unsupported():
    assert extract_text("a.md", b"# hi") == "# hi"
    assert extract_text("a.TXT", b"yo") == "yo"
    try:
        extract_text("a.exe", b"x")
        raise AssertionError("should fail")
    except ValueError:
        pass


def test_pdf_to_text():
    pdf = make_pdf("Refunds take seven days")
    assert "Refunds take seven days" in PdfReader(io.BytesIO(pdf)).pages[0].extract_text()
    assert "Refunds take seven days" in extract_text("x.pdf", pdf)


def test_retrieval_ranks_relevant_chunk_first(db_session):
    t, _ = create_tenant(db_session, "t")
    emb = FakeProvider()
    ingest_document(
        db_session,
        t.id,
        emb,
        "kb.md",
        "# Refunds\n\nFull refund within 30 days.\n\n# Shipping\n\nExpress shipping costs 15 dollars.",
    )
    hits = retrieve(db_session, t.id, emb, "how much is express shipping", 2)
    assert hits[0].doc == "kb.md" and "15 dollars" in hits[0].text
    assert hits[0].score > hits[1].score


def test_reingest_replaces_document(db_session):
    t, _ = create_tenant(db_session, "t")
    emb = FakeProvider()
    ingest_document(db_session, t.id, emb, "kb.md", "old text about apples")
    ingest_document(db_session, t.id, emb, "kb.md", "new text about pears")
    hits = retrieve(db_session, t.id, emb, "apples", 5)
    assert len(hits) == 1 and "pears" in hits[0].text


def test_upload_pdf_endpoint_and_search(env):
    t = env.tenant()
    r = env.client.post(
        "/v1/documents/upload",
        headers=t["headers"],
        files={"file": ("guide.pdf", make_pdf("Warranty lasts two years"), "application/pdf")},
    )
    assert r.status_code == 201, r.text
    hits = env.client.post("/v1/search", json={"query": "warranty"}, headers=t["headers"]).json()["hits"]
    assert hits[0]["doc"] == "guide.pdf"


def test_upload_rejects_bad_type_and_corrupt_pdf(env):
    t = env.tenant()
    r = env.client.post("/v1/documents/upload", headers=t["headers"], files={"file": ("a.exe", b"x")})
    assert r.status_code == 422
    r = env.client.post("/v1/documents/upload", headers=t["headers"], files={"file": ("a.pdf", b"not a pdf")})
    assert r.status_code == 422
