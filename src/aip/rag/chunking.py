import re


def chunk_text(text: str, max_chars: int = 500, overlap: int = 50) -> list[str]:
    """Split text into chunks of about max_chars.

    Paragraphs are packed together. Markdown headings are remembered and put at the top of
    each chunk so a chunk keeps its context. Very long paragraphs are cut with a small overlap.
    """
    chunks: list[str] = []
    heading = ""
    buf = ""

    def flush() -> None:
        nonlocal buf
        if buf.strip():
            chunks.append((f"{heading}\n{buf}" if heading else buf).strip())
        buf = ""

    for para in re.split(r"\n\s*\n", text.strip()):
        para = para.strip()
        if not para:
            continue
        if para.startswith("#"):
            flush()
            heading = para.splitlines()[0]
            para = "\n".join(para.splitlines()[1:]).strip()
            if not para:
                continue
        while len(para) > max_chars:  # hard cut for long paragraphs
            flush()
            buf = para[:max_chars]
            flush()
            para = para[max_chars - overlap :]
        if len(buf) + len(para) + 2 > max_chars:
            flush()
        buf = f"{buf}\n\n{para}" if buf else para
    flush()
    return chunks
