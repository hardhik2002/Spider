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


def _split_long(sentence: str, tokenizer: Tokenizer, max_tokens: int) -> list[str]:
    words = sentence.split()
    if not words:
        return []
    output = []
    start = 0
    while start < len(words):
        low, high = start + 1, len(words)
        best = start
        while low <= high:
            middle = (low + high) // 2
            candidate = " ".join(words[start:middle])
            if len(tokenizer.encode(candidate)) <= max_tokens:
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        if best == start:
            # A single pathological word exceeds the limit; token splitting is unavoidable.
            ids = tokenizer.encode(words[start])
            output.extend(
                tokenizer.decode(ids[i : i + max_tokens]) for i in range(0, len(ids), max_tokens)
            )
            start += 1
        else:
            output.append(" ".join(words[start:best]))
            start = best
    return output


def _overlap_suffix(text: str, tokenizer: Tokenizer, budget: int) -> str:
    words = text.split()
    low, high = 0, min(len(words), budget)
    best = 0
    while low <= high:
        middle = (low + high) // 2
        candidate = " ".join(words[-middle:]) if middle else ""
        if len(tokenizer.encode(candidate)) <= budget:
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    return " ".join(words[-best:]) if best else ""


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
    """Keep paragraph/sentence and word boundaries while enforcing tokenizer limits."""
    if not text.strip():
        return []
    units: list[tuple[str | None, str]] = []
    for heading, paragraph in _sections(text, title):
        if len(tokenizer.encode(paragraph)) <= target_tokens:
            units.append((heading, paragraph))
            continue
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", paragraph):
            if len(tokenizer.encode(sentence)) <= max_tokens:
                units.append((heading, sentence))
            else:
                units.extend(
                    (heading, piece) for piece in _split_long(sentence, tokenizer, max_tokens)
                )

    output: list[Passage] = []
    current = ""
    current_heading: str | None = None

    def emit() -> None:
        if current:
            output.append(
                Passage(current, current_heading, len(tokenizer.encode(current)), len(output))
            )

    for heading, unit in units:
        proposed = (current + " " + unit).strip()
        if current and (
            len(tokenizer.encode(proposed)) > target_tokens or heading != current_heading
        ):
            previous = current
            emit()
            overlap = (
                _overlap_suffix(previous, tokenizer, overlap_tokens)
                if (overlap_tokens and heading == current_heading)
                else ""
            )
            proposed = (overlap + " " + unit).strip()
            current = proposed if len(tokenizer.encode(proposed)) <= max_tokens else unit
            current_heading = heading
        elif not current:
            current_heading = heading
            current = unit
        else:
            current = proposed
        if len(tokenizer.encode(current)) >= max_tokens:
            emit()
            current = ""
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
