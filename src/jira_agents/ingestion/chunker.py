"""Character-based chunker with overlap — keeps citations stable."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Chunk:
    section: str
    index: int  # global index within ticket
    content: str


def chunk_text(text: str, section: str, start_index: int, size: int, overlap: int) -> tuple[list[Chunk], int]:
    text = (text or "").strip()
    if not text:
        return [], start_index
    chunks: list[Chunk] = []
    idx = start_index
    step = max(1, size - overlap)
    for pos in range(0, len(text), step):
        piece = text[pos : pos + size].strip()
        if not piece:
            continue
        chunks.append(Chunk(section=section, index=idx, content=piece))
        idx += 1
        if pos + size >= len(text):
            break
    return chunks, idx


def chunk_ticket(summary: str, description: str | None, resolution: str | None,
                 size: int = 1200, overlap: int = 200) -> list[Chunk]:
    """Section-aware chunking: summary -> body -> resolution."""
    all_chunks: list[Chunk] = []
    idx = 0
    if summary:
        c, idx = chunk_text(f"Summary: {summary}", "summary", idx, size, overlap)
        all_chunks.extend(c)
    if description:
        c, idx = chunk_text(f"Description:\n{description}", "body", idx, size, overlap)
        all_chunks.extend(c)
    if resolution:
        c, idx = chunk_text(f"Resolution:\n{resolution}", "resolution", idx, size, overlap)
        all_chunks.extend(c)
    return all_chunks


def chunk_snow_incident(short_description: str, description: str | None,
                        work_notes: str | None, close_notes: str | None,
                        size: int = 1200, overlap: int = 200) -> list[Chunk]:
    """Section-aware chunking for ServiceNow incidents.

    Order mirrors the JIRA chunker: short_description -> description ->
    work_notes (journal trail, often long) -> close_notes (the resolution).
    """
    all_chunks: list[Chunk] = []
    idx = 0
    if short_description:
        c, idx = chunk_text(f"Short description: {short_description}",
                            "short_description", idx, size, overlap)
        all_chunks.extend(c)
    if description:
        c, idx = chunk_text(f"Description:\n{description}", "description", idx, size, overlap)
        all_chunks.extend(c)
    if work_notes:
        c, idx = chunk_text(f"Work notes:\n{work_notes}", "work_notes", idx, size, overlap)
        all_chunks.extend(c)
    if close_notes:
        c, idx = chunk_text(f"Close notes:\n{close_notes}", "close_notes", idx, size, overlap)
        all_chunks.extend(c)
    return all_chunks
