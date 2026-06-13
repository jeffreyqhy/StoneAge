from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Iterable


Coord = tuple[int, int]
ReadOnce = Callable[[], Iterable[int] | None]


@dataclass(frozen=True)
class CoordinateReading:
    coord: Coord | None
    confidence: float
    samples: list[Coord]
    accepted_samples: int


def coord_distance(a: Coord, b: Coord) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


class GameCoordReader:
    def __init__(
        self,
        read_once: ReadOnce,
        *,
        sample_count: int = 3,
        min_agreement: int = 2,
        sample_delay: float = 0.12,
        max_spread: int = 1,
    ) -> None:
        self.read_once = read_once
        self.sample_count = max(1, int(sample_count))
        self.min_agreement = min(self.sample_count, max(1, int(min_agreement)))
        self.sample_delay = max(0.0, float(sample_delay))
        self.max_spread = max(0, int(max_spread))

    def read(self, *, previous: Iterable[int] | None = None, max_jump: int | None = None) -> CoordinateReading:
        previous_coord = self._coord(previous) if previous is not None else None
        samples: list[Coord] = []
        for index in range(self.sample_count):
            value = self._coord(self.read_once())
            if value is not None and self._plausible(value, previous_coord, max_jump):
                samples.append(value)
            if index + 1 < self.sample_count and self.sample_delay:
                time.sleep(self.sample_delay)
        if not samples:
            return CoordinateReading(None, 0.0, [], 0)
        counts = Counter(samples)
        coord, count = counts.most_common(1)[0]
        if count >= self.min_agreement:
            return CoordinateReading(coord, min(1.0, count / self.sample_count), samples, count)
        clustered = self._cluster(samples)
        if clustered is None:
            return CoordinateReading(None, 0.0, samples, 0)
        confidence = max(0.34, len(samples) / max(1, self.sample_count) * 0.55)
        return CoordinateReading(clustered, confidence, samples, len(samples))

    def _cluster(self, samples: list[Coord]) -> Coord | None:
        for coord in samples:
            neighbors = [sample for sample in samples if coord_distance(coord, sample) <= self.max_spread]
            if len(neighbors) >= self.min_agreement:
                x = round(sum(item[0] for item in neighbors) / len(neighbors))
                y = round(sum(item[1] for item in neighbors) / len(neighbors))
                return int(x), int(y)
        if self.sample_count == 1:
            return samples[0]
        return None

    def _coord(self, value: Iterable[int] | None) -> Coord | None:
        if value is None:
            return None
        try:
            x, y = value
        except (TypeError, ValueError):
            return None
        return int(x), int(y)

    def _plausible(self, coord: Coord, previous: Coord | None, max_jump: int | None) -> bool:
        if previous is None or max_jump is None:
            return True
        return coord_distance(coord, previous) <= max(1, int(max_jump))
