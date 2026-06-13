from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..maps.walkability_grid import Coord, WalkabilityGrid, as_coord
from .path_planner import PathPlan, PathPlanner, manhattan


@dataclass(frozen=True)
class ApproachCandidate:
    coord: Coord
    plan: PathPlan
    score: float
    target_distance: int


class ApproachPointSelector:
    def __init__(self, planner: PathPlanner | None = None) -> None:
        self.planner = planner or PathPlanner()

    def select(
        self,
        start: Iterable[int],
        target: Iterable[int],
        grid: WalkabilityGrid,
        *,
        tolerance: int = 0,
        approach_radius: int = 1,
        max_radius: int = 3,
        lookahead: int = 5,
        allow_unknown: bool = True,
    ) -> ApproachCandidate:
        start_node = as_coord(start)
        target_node = as_coord(target)
        candidates = self._candidate_points(
            target_node,
            tolerance=max(0, int(tolerance)),
            approach_radius=max(0, int(approach_radius)),
            max_radius=max(0, int(max_radius)),
        )
        best: ApproachCandidate | None = None
        for coord in candidates:
            if grid.is_blocked(coord) or grid.is_danger(coord, threshold=4):
                continue
            plan = self.planner.plan(start_node, coord, grid, allow_unknown=allow_unknown, lookahead=lookahead)
            if not plan.path:
                continue
            path_cost = len(plan.path) - 1
            danger = grid.danger_nodes.get(coord, 0)
            target_distance = manhattan(coord, target_node)
            known_bonus = -0.8 if grid.is_walkable(coord) else 0.0
            score = path_cost * 2.0 + target_distance * 1.2 + danger * 6.0 + known_bonus
            candidate = ApproachCandidate(coord=coord, plan=plan, score=score, target_distance=target_distance)
            if best is None or candidate.score < best.score:
                best = candidate
        if best is not None:
            return best
        plan = self.planner.plan(start_node, target_node, grid, allow_unknown=True, lookahead=lookahead)
        return ApproachCandidate(coord=target_node, plan=plan, score=float(len(plan.path)), target_distance=0)

    def _candidate_points(
        self,
        target: Coord,
        *,
        tolerance: int,
        approach_radius: int,
        max_radius: int,
    ) -> list[Coord]:
        radius = max(tolerance, approach_radius)
        outer = max(radius, max_radius)
        points: list[Coord] = []
        start_distance = 1 if approach_radius > 0 else 0
        for distance in range(start_distance, outer + 1):
            if distance > radius and distance > max_radius:
                break
            for dx in range(-distance, distance + 1):
                dy_abs = distance - abs(dx)
                for dy in ({-dy_abs, dy_abs} if dy_abs else {0}):
                    point = (target[0] + dx, target[1] + dy)
                    if point not in points:
                        points.append(point)
        points.sort(key=lambda coord: (manhattan(coord, target), abs(coord[0] - target[0]), abs(coord[1] - target[1])))
        return points
