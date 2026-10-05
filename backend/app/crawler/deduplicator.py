import hashlib


def content_hash(text: str) -> str:
    normalized = " ".join(text.split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class ContentDeduplicator:
    def __init__(self) -> None:
        self._hashes: dict[str, int] = {}

    def find_or_add(self, digest: str, page_id: int) -> int | None:
        existing = self._hashes.get(digest)
        if existing is None:
            self._hashes[digest] = page_id
        return existing
