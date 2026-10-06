"""Structure-aware, tokenizer-bounded passage chunking."""

import re
from dataclasses import dataclass
from typing import Protocol


class Tokenizer(Protocol):
    def encode(self, text: str) -> list[int]: ...
    def decode(self, tokens: list[int]) -> str: ...


class BGETokenizer:
    def __init__(self, model_name: str = "BAAI/bge-m3") -> None:
        self.model_name = model_name
        self._tokenizer = None

    def _get(self):
        if self._tokenizer is None:
            from transformers import AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        return self._tokenizer

    def encode(self, text: str) -> list[int]:
        return self._get().encode(text, add_special_tokens=False, verbose=False)

    def decode(self, tokens: list[int]) -> str:
        return self._get().decode(tokens, skip_special_tokens=True).strip()


@dataclass(frozen=True)
class Passage:
    text: str
    heading: str | None
    token_count: int
    index: int


def _sections(text: str, title: str | None):
    heading = title
    for block in re.split(r"\n\s*\n", text.strip()):
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        if len(lines) == 1 and (block.startswith("# ") or block.startswith("## ")):
            heading = block.lstrip("# ").strip()
            continue
        if len(lines) > 1 and lines[0].startswith("# "):
            heading = lines[0].lstrip("# ").strip()
            block = "\n".join(lines[1:]).strip()
        if block:
            yield heading, block


def chunk_text(
    text: str,
    tokenizer: Tokenizer,
    *,
    title: str | None = None,
    target_tokens: int = 500,
    max_tokens: int = 650,
    overlap_tokens: int = 80,
    min_tokens: int = 80,
) -> list[Passage]:
    """Keep paragraph boundaries when possible, then split long sentences by tokens."""
    if not text.strip():
        return []
    units: list[tuple[str | None, list[int]]] = []
    for heading, paragraph in _sections(text, title):
        ids = tokenizer.encode(paragraph)
        if len(ids) <= target_tokens:
            units.append((heading, ids))
            continue
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", paragraph):
            sentence_ids = tokenizer.encode(sentence)
            for start in range(0, len(sentence_ids), max_tokens):
                piece = sentence_ids[start : start + max_tokens]
                if piece:
                    units.append((heading, piece))

    output: list[Passage] = []
    current: list[int] = []
    current_heading: str | None = None

    def emit() -> None:
        if current:
            value = tokenizer.decode(current).strip()
            if value:
                output.append(Passage(value, current_heading, len(current), len(output)))

    for heading, ids in units:
        if current and (len(current) + len(ids) > target_tokens or heading != current_heading):
            previous = current
            emit()
            overlap = (
                previous[-overlap_tokens:] if overlap_tokens and heading == current_heading else []
            )
            current = overlap if len(overlap) + len(ids) <= max_tokens else []
        if not current:
            current_heading = heading
        current.extend(ids)
        if len(current) >= max_tokens:
            emit()
            current = []
    emit()
    if len(output) > 1 and output[-1].token_count < min_tokens:
        last = output.pop()
        prior = output[-1]
        merged = tokenizer.encode(prior.text + "\n" + last.text)
        if len(merged) <= max_tokens and prior.heading == last.heading:
            output[-1] = Passage(tokenizer.decode(merged), prior.heading, len(merged), prior.index)
        else:
            output.append(last)
    return [Passage(p.text, p.heading, p.token_count, i) for i, p in enumerate(output)]
