from collections import deque

from app.crawler.models import FrontierItem


class Frontier:
    """FIFO policy behind a small interface; a scored heap can replace it later."""

    def __init__(self) -> None:
        self._items: deque[FrontierItem] = deque()
        self._seen: set[str] = set()

    def push(self, item: FrontierItem) -> bool:
        if item.normalized_url in self._seen:
            return False
        self._seen.add(item.normalized_url)
        self._items.append(item)
        return True

    def pop(self) -> FrontierItem:
        return self._items.popleft()

    def __bool__(self) -> bool:
        return bool(self._items)

    def __len__(self) -> int:
        return len(self._items)
