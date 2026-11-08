import io

from pypdf import PdfReader


def extract_text(filename: str, data: bytes) -> str:
    """Return plain text from .md, .txt or .pdf bytes."""
    name = filename.lower()
    if name.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    if name.endswith((".md", ".markdown", ".txt")):
        return data.decode("utf-8", errors="replace")
    raise ValueError("unsupported file type: use .md, .txt or .pdf")
