from __future__ import annotations

import heapq
from collections import deque
from dataclasses import dataclass
from typing import Iterable

from ..maps.walkability_grid import Coord, WalkabilityGrid, as_coord


NEIGHBORS_8: tuple[Coord, ...] = (
    (-1, -1),
    (0, -1),
    (1, -1),
    (-1, 0),
    (1, 0),
    (-1, 1),
    (0, 1),
    (1, 1),
)


@dataclass(frozen=True)
class PathPlan:
    start: Coord
    goal: Coord
    path: list[Coord]
    waypoint: Coord | None
    cost: float
    used_unknown: bool
    status: str


def manhattan(a: Coord, b: Coord) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def chebyshev(a: Coord, b: Coord) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


class PathPlanner:
    def __init__(self, *, diagonal: bool = True, max_expansions: int = 6000) -> None:
        self.diagonal = diagonal
        self.max_expansions = max_expansions

    def plan(
        self,
        start: Iterable[int],
        goal: Iterable[int],
        grid: WalkabilityGrid,
        *,
        allow_unknown: bool = True,
        lookahead: int = 5,
        method: str = "astar",
    ) -> PathPlan:
        start_node = as_coord(start)
        goal_node = as_coord(goal)
        if start_node == goal_node:
            return PathPlan(start_node, goal_node, [start_node], start_node, 0.0, False, "arrived")
        if not grid.blocked_nodes and not grid.danger_nodes:
            path = self._direct_path(start_node, goal_node)
            return PathPlan(
                start_node,
                goal_node,
                path,
                self.select_waypoint(path, lookahead),
                float(len(path) - 1),
                True,
                "direct",
            )
        if method.lower() == "bfs":
            path = self._bfs(start_node, goal_node, grid, allow_unknown=allow_unknown)
        else:
            path = self._astar(start_node, goal_node, grid, allow_unknown=allow_unknown)
        if not path:
            path = self._direct_path(start_node, goal_node)
            return PathPlan(
                start_node,
                goal_node,
                path,
                self.select_waypoint(path, lookahead),
                float(len(path) - 1),
                True,
                "fallback_direct",
            )
        used_unknown = any(not grid.is_known(node) for node in path)
        return PathPlan(
            start_node,
            goal_node,
            path,
            self.select_waypoint(path, lookahead),
            float(len(path) - 1),
            used_unknown,
            "planned",
        )

    def select_waypoint(self, path: list[Coord], lookahead: int = 5) -> Coord | None:
        if not path:
            return None
        if len(path) == 1:
            return path[0]
        index = max(1, min(int(lookahead), len(path) - 1))
        return path[index]

    def _astar(
        self,
        start: Coord,
        goal: Coord,
        grid: WalkabilityGrid,
        *,
        allow_unknown: bool,
    ) -> list[Coord]:
        min_x = min(start[0], goal[0]) - max(10, manhattan(start, goal) // 2 + 8)
        max_x = max(start[0], goal[0]) + max(10, manhattan(start, goal) // 2 + 8)
        min_y = min(start[1], goal[1]) - max(10, manhattan(start, goal) // 2 + 8)
        max_y = max(start[1], goal[1]) + max(10, manhattan(start, goal) // 2 + 8)
        open_heap: list[tuple[float, float, Coord]] = []
        heapq.heappush(open_heap, (float(chebyshev(start, goal)), 0.0, start))
        came_from: dict[Coord, Coord] = {}
        cost_so_far: dict[Coord, float] = {start: 0.0}
        expansions = 0
        while open_heap and expansions < self.max_expansions:
            _, current_cost, current = heapq.heappop(open_heap)
            expansions += 1
            if current == goal:
                return self._reconstruct(came_from, current)
            if current_cost > cost_so_far.get(current, float("inf")):
                continue
            for neighbor in self._neighbors(current):
                if not (min_x <= neighbor[0] <= max_x and min_y <= neighbor[1] <= max_y):
                    continue
                if not self._allowed(neighbor, goal, grid, allow_unknown=allow_unknown):
                    continue
                step_cost = 1.4 if neighbor[0] != current[0] and neighbor[1] != current[1] else 1.0
                step_cost += grid.node_penalty(neighbor)
                new_cost = current_cost + step_cost
                if new_cost >= cost_so_far.get(neighbor, float("inf")):
                    continue
                cost_so_far[neighbor] = new_cost
                priority = new_cost + chebyshev(neighbor, goal)
                heapq.heappush(open_heap, (priority, new_cost, neighbor))
                came_from[neighbor] = current
        return []

    def _bfs(self, start: Coord, goal: Coord, grid: WalkabilityGrid, *, allow_unknown: bool) -> list[Coord]:
        queue: deque[Coord] = deque([start])
        came_from: dict[Coord, Coord | None] = {start: None}
        expansions = 0
        margin = max(12, manhattan(start, goal) + 8)
        min_x = min(start[0], goal[0]) - margin
        max_x = max(start[0], goal[0]) + margin
        min_y = min(start[1], goal[1]) - margin
        max_y = max(start[1], goal[1]) + margin
        while queue and expansions < self.max_expansions:
            current = queue.popleft()
            expansions += 1
            if current == goal:
                return self._reconstruct({k: v for k, v in came_from.items() if v is not None}, current)
            for neighbor in self._neighbors(current):
                if neighbor in came_from:
                    continue
                if not (min_x <= neighbor[0] <= max_x and min_y <= neighbor[1] <= max_y):
                    continue
                if not self._allowed(neighbor, goal, grid, allow_unknown=allow_unknown):
                    continue
                came_from[neighbor] = current
                queue.append(neighbor)
        return []

    def _neighbors(self, coord: Coord) -> list[Coord]:
        if self.diagonal:
            deltas = NEIGHBORS_8
        else:
            deltas = ((0, -1), (-1, 0), (1, 0), (0, 1))
        return [(coord[0] + dx, coord[1] + dy) for dx, dy in deltas]

    def _allowed(self, coord: Coord, goal: Coord, grid: WalkabilityGrid, *, allow_unknown: bool) -> bool:
        if coord == goal:
            return not grid.is_blocked(coord)
        if grid.is_blocked(coord):
            return False
        if grid.is_danger(coord, threshold=4):
            return False
        return allow_unknown or grid.is_walkable(coord)

    def _reconstruct(self, came_from: dict[Coord, Coord], current: Coord) -> list[Coord]:
        path = [current]
        while current in came_from:
            current = came_from[current]
            path.append(current)
        path.reverse()
        return path

    def _direct_path(self, start: Coord, goal: Coord) -> list[Coord]:
        path = [start]
        x, y = start
        while (x, y) != goal:
            if x < goal[0]:
                x += 1
            elif x > goal[0]:
                x -= 1
            if y < goal[1]:
                y += 1
            elif y > goal[1]:
                y -= 1
            path.append((x, y))
        return path

