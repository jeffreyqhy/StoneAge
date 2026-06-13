from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..maps.walkability_grid import Coord, as_coord
from .path_planner import manhattan


def within_tolerance(a: Coord, b: Coord, tolerance: int) -> bool:
    return abs(a[0] - b[0]) <= tolerance and abs(a[1] - b[1]) <= tolerance


@dataclass(frozen=True)
class MovementOutcome:
    before: Coord
    after: Coord
    waypoint: Coord
    target: Coord
    success: bool
    stuck: bool
    farther: bool
    progress_score: float
    reason: str
    consecutive_stuck: int


class StuckDetector:
    def __init__(self, *, consecutive_limit: int = 2) -> None:
        self.consecutive_limit = max(1, int(consecutive_limit))
        self.consecutive_stuck = 0
        self.blocked_strategies: set[str] = set()

    def evaluate(
        self,
        before: Iterable[int],
        after: Iterable[int],
        *,
        waypoint: Iterable[int],
        target: Iterable[int],
        tolerance: int,
    ) -> MovementOutcome:
        before_node = as_coord(before)
        after_node = as_coord(after)
        waypoint_node = as_coord(waypoint)
        target_node = as_coord(target)
        before_waypoint_distance = manhattan(before_node, waypoint_node)
        after_waypoint_distance = manhattan(after_node, waypoint_node)
        waypoint_progress = before_waypoint_distance - after_waypoint_distance
        before_target_distance = manhattan(before_node, target_node)
        after_target_distance = manhattan(after_node, target_node)
        target_progress = before_target_distance - after_target_distance
        progress_score = max(float(target_progress), float(waypoint_progress) * 0.5)
        no_change = before_node == after_node
        farther = target_progress < 0 and waypoint_progress <= 0
        tolerance = max(0, int(tolerance))
        arrived = within_tolerance(after_node, waypoint_node, tolerance) or within_tolerance(
            after_node,
            target_node,
            tolerance,
        )
        stuck = no_change or (target_progress <= 0 and waypoint_progress <= 0 and not arrived)
        if stuck:
            self.consecutive_stuck += 1
        else:
            self.consecutive_stuck = 0
        if arrived:
            reason = "arrived"
        elif no_change:
            reason = "no_coord_change"
        elif farther:
            reason = "moved_farther"
        elif target_progress <= 0 and waypoint_progress <= 0:
            reason = "no_progress"
        else:
            reason = "progress"
        return MovementOutcome(
            before=before_node,
            after=after_node,
            waypoint=waypoint_node,
            target=target_node,
            success=not stuck and (target_progress > 0 or waypoint_progress > 0 or arrived),
            stuck=stuck or self.consecutive_stuck >= self.consecutive_limit,
            farther=farther,
            progress_score=progress_score,
            reason=reason,
            consecutive_stuck=self.consecutive_stuck,
        )

    def mark_strategy_failed(self, strategy_key: str) -> None:
        if strategy_key:
            self.blocked_strategies.add(strategy_key)

    def clear_strategy_failures(self) -> None:
        self.blocked_strategies.clear()
