import heapq
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


class PriorityFrontier:
    """Highest priority first, with deterministic periodic low-priority exploration."""

    def __init__(self, exploration_rate: float = 0.0) -> None:
        self._high: list[tuple[float, int, int, str]] = []
        self._low: list[tuple[float, int, int, str]] = []
        self._active: dict[str, FrontierItem] = {}
        self._seen: set[str] = set()
        self._pops = 0
        self._exploration_interval = round(1 / exploration_rate) if exploration_rate > 0 else None
        self.last_pop_exploration = False

    def push(self, item: FrontierItem) -> bool:
        if item.normalized_url in self._seen:
            return False
        self._seen.add(item.normalized_url)
        self._active[item.normalized_url] = item
        score = item.priority_score if item.priority_score is not None else item.priority
        heapq.heappush(
            self._high, (-score, item.depth, item.discovery_order, item.normalized_url)
        )
        heapq.heappush(
            self._low, (score, item.depth, item.discovery_order, item.normalized_url)
        )
        return True

    def pop(self) -> FrontierItem:
        if not self._active:
            raise IndexError("pop from empty frontier")
        self._pops += 1
        explore = bool(
            self._exploration_interval
            and self._pops % self._exploration_interval == 0
            and len(self._active) > 1
        )
        heap = self._low if explore else self._high
        while heap:
            *_, url = heapq.heappop(heap)
            if url in self._active:
                self.last_pop_exploration = explore
                return self._active.pop(url)
        raise RuntimeError("Priority frontier heap is inconsistent")

    def __bool__(self) -> bool:
        return bool(self._active)

    def __len__(self) -> int:
        return len(self._active)
