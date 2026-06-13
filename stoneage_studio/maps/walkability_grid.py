from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


Coord = tuple[int, int]
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


def as_coord(value: Iterable[int] | Coord) -> Coord:
    x, y = value
    return int(x), int(y)


def coord_key(coord: Coord) -> str:
    return f"{int(coord[0])},{int(coord[1])}"


def coord_from_key(value: str) -> Coord:
    x, y = value.split(",", 1)
    return int(x), int(y)


def interpolate_coords(start: Coord, end: Coord) -> list[Coord]:
    x, y = start
    target_x, target_y = end
    points = [(x, y)]
    while (x, y) != (target_x, target_y):
        if x < target_x:
            x += 1
        elif x > target_x:
            x -= 1
        if y < target_y:
            y += 1
        elif y > target_y:
            y -= 1
        points.append((x, y))
    return points


@dataclass
class WalkabilityGrid:
    map_id: str
    walkable_nodes: set[Coord] = field(default_factory=set)
    blocked_nodes: set[Coord] = field(default_factory=set)
    unknown_nodes: set[Coord] = field(default_factory=set)
    danger_nodes: dict[Coord, int] = field(default_factory=dict)
    edge_failures: dict[str, int] = field(default_factory=dict)
    edge_successes: dict[str, int] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.time)

    @classmethod
    def load(cls, path: str | Path, map_id: str) -> "WalkabilityGrid":
        source = Path(path)
        if not source.exists():
            return cls(map_id=map_id)
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls(map_id=map_id)
        return cls(
            map_id=str(payload.get("map_id") or map_id),
            walkable_nodes={coord_from_key(value) for value in payload.get("walkable_nodes", [])},
            blocked_nodes={coord_from_key(value) for value in payload.get("blocked_nodes", [])},
            unknown_nodes={coord_from_key(value) for value in payload.get("unknown_nodes", [])},
            danger_nodes={
                coord_from_key(key): int(value)
                for key, value in (payload.get("danger_nodes") or {}).items()
            },
            edge_failures={str(key): int(value) for key, value in (payload.get("edge_failures") or {}).items()},
            edge_successes={str(key): int(value) for key, value in (payload.get("edge_successes") or {}).items()},
            updated_at=float(payload.get("updated_at") or time.time()),
        )

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")

    def to_json(self) -> dict[str, Any]:
        return {
            "map_id": self.map_id,
            "walkable_nodes": sorted(coord_key(coord) for coord in self.walkable_nodes),
            "blocked_nodes": sorted(coord_key(coord) for coord in self.blocked_nodes),
            "unknown_nodes": sorted(coord_key(coord) for coord in self.unknown_nodes),
            "danger_nodes": {
                coord_key(coord): count
                for coord, count in sorted(self.danger_nodes.items(), key=lambda item: item[0])
            },
            "edge_failures": dict(sorted(self.edge_failures.items())),
            "edge_successes": dict(sorted(self.edge_successes.items())),
            "updated_at": self.updated_at,
        }

    def is_blocked(self, coord: Iterable[int] | Coord) -> bool:
        node = as_coord(coord)
        return node in self.blocked_nodes

    def is_danger(self, coord: Iterable[int] | Coord, *, threshold: int = 2) -> bool:
        return self.danger_nodes.get(as_coord(coord), 0) >= threshold

    def is_walkable(self, coord: Iterable[int] | Coord) -> bool:
        node = as_coord(coord)
        return node in self.walkable_nodes and node not in self.blocked_nodes

    def is_known(self, coord: Iterable[int] | Coord) -> bool:
        node = as_coord(coord)
        return node in self.walkable_nodes or node in self.blocked_nodes or node in self.unknown_nodes

    def node_penalty(self, coord: Iterable[int] | Coord) -> float:
        node = as_coord(coord)
        return float(self.danger_nodes.get(node, 0) * 3 + (0 if self.is_known(node) else 1))

    def neighbors8(self, coord: Iterable[int] | Coord) -> list[Coord]:
        x, y = as_coord(coord)
        return [(x + dx, y + dy) for dx, dy in NEIGHBORS_8]

    def unknown_neighbors(self, coord: Iterable[int] | Coord) -> list[Coord]:
        return [
            node
            for node in self.neighbors8(coord)
            if not self.is_known(node) and not self.is_danger(node, threshold=4)
        ]

    def frontier_nodes(self) -> list[Coord]:
        return sorted(
            node
            for node in self.walkable_nodes
            if node not in self.blocked_nodes and self.unknown_neighbors(node)
        )

    def bounds(self) -> tuple[int, int, int, int] | None:
        nodes = set(self.walkable_nodes) | set(self.blocked_nodes) | set(self.unknown_nodes) | set(self.danger_nodes)
        if not nodes:
            return None
        xs = [node[0] for node in nodes]
        ys = [node[1] for node in nodes]
        return min(xs), min(ys), max(xs), max(ys)

    def summary(self) -> dict[str, Any]:
        bounds = self.bounds()
        return {
            "map_id": self.map_id,
            "walkable": len(self.walkable_nodes),
            "blocked": len(self.blocked_nodes),
            "unknown": len(self.unknown_nodes),
            "danger": len(self.danger_nodes),
            "frontier": len(self.frontier_nodes()),
            "bounds": list(bounds) if bounds else None,
            "updated_at": self.updated_at,
        }

    def prune_outliers(self, *, max_axis_gap: int = 12, min_nodes: int = 30) -> int:
        nodes = set(self.walkable_nodes) | set(self.blocked_nodes) | set(self.unknown_nodes) | set(self.danger_nodes)
        if len(nodes) < int(min_nodes):
            return 0
        x_range = self._dominant_axis_range([node[0] for node in nodes], max_gap=max_axis_gap)
        y_range = self._dominant_axis_range([node[1] for node in nodes], max_gap=max_axis_gap)
        if x_range is None and y_range is None:
            return 0

        def keep(node: Coord) -> bool:
            if x_range is not None and not (x_range[0] <= node[0] <= x_range[1]):
                return False
            if y_range is not None and not (y_range[0] <= node[1] <= y_range[1]):
                return False
            return True

        before = len(nodes)
        self.walkable_nodes = {node for node in self.walkable_nodes if keep(node)}
        self.blocked_nodes = {node for node in self.blocked_nodes if keep(node)}
        self.unknown_nodes = {node for node in self.unknown_nodes if keep(node)}
        self.danger_nodes = {node: count for node, count in self.danger_nodes.items() if keep(node)}
        removed = before - len(set(self.walkable_nodes) | set(self.blocked_nodes) | set(self.unknown_nodes) | set(self.danger_nodes))
        if removed > 0:
            self.updated_at = time.time()
        return removed

    def prune_to_bounds(self, bounds: tuple[int, int, int, int] | None) -> int:
        if bounds is None:
            return 0
        min_x, min_y, max_x, max_y = (int(value) for value in bounds)
        nodes = set(self.walkable_nodes) | set(self.blocked_nodes) | set(self.unknown_nodes) | set(self.danger_nodes)
        if not nodes:
            return 0

        def keep(node: Coord) -> bool:
            return min_x <= node[0] <= max_x and min_y <= node[1] <= max_y

        before = len(nodes)
        self.walkable_nodes = {node for node in self.walkable_nodes if keep(node)}
        self.blocked_nodes = {node for node in self.blocked_nodes if keep(node)}
        self.unknown_nodes = {node for node in self.unknown_nodes if keep(node)}
        self.danger_nodes = {node: count for node, count in self.danger_nodes.items() if keep(node)}
        removed = before - len(set(self.walkable_nodes) | set(self.blocked_nodes) | set(self.unknown_nodes) | set(self.danger_nodes))
        if removed > 0:
            self.updated_at = time.time()
        return removed

    def _dominant_axis_range(self, values: list[int], *, max_gap: int) -> tuple[int, int] | None:
        if not values:
            return None
        sorted_values = sorted(int(value) for value in values)
        segments: list[list[int]] = [[sorted_values[0]]]
        for value in sorted_values[1:]:
            if value - segments[-1][-1] > int(max_gap):
                segments.append([value])
            else:
                segments[-1].append(value)
        if len(segments) <= 1:
            return None
        dominant = max(segments, key=len)
        if len(dominant) < len(sorted_values) * 0.6:
            return None
        return dominant[0], dominant[-1]

    def ascii_map(self, *, padding: int = 1, max_width: int = 80, max_height: int = 48) -> str:
        bounds = self.bounds()
        if bounds is None:
            return "(空地图)"
        min_x, min_y, max_x, max_y = bounds
        min_x -= max(0, int(padding))
        min_y -= max(0, int(padding))
        max_x += max(0, int(padding))
        max_y += max(0, int(padding))
        if max_x - min_x + 1 > max_width:
            max_x = min_x + max_width - 1
        if max_y - min_y + 1 > max_height:
            max_y = min_y + max_height - 1
        rows: list[str] = []
        rows.append(f"x {min_x}..{max_x}, y {min_y}..{max_y}")
        for y in range(min_y, max_y + 1):
            chars: list[str] = []
            for x in range(min_x, max_x + 1):
                node = (x, y)
                if node in self.blocked_nodes:
                    chars.append("#")
                elif self.is_danger(node, threshold=4):
                    chars.append("!")
                elif node in self.walkable_nodes:
                    chars.append(".")
                elif node in self.unknown_nodes:
                    chars.append("?")
                else:
                    chars.append(" ")
            rows.append(f"{y:>3} {''.join(chars)}")
        return "\n".join(rows)

    def mark_walkable(self, coord: Iterable[int] | Coord) -> None:
        node = as_coord(coord)
        self.walkable_nodes.add(node)
        self.blocked_nodes.discard(node)
        self.unknown_nodes.discard(node)
        self.updated_at = time.time()

    def mark_blocked(self, coord: Iterable[int] | Coord) -> None:
        node = as_coord(coord)
        self.blocked_nodes.add(node)
        self.walkable_nodes.discard(node)
        self.unknown_nodes.discard(node)
        self.updated_at = time.time()

    def mark_unknown(self, coord: Iterable[int] | Coord) -> None:
        node = as_coord(coord)
        if node not in self.walkable_nodes and node not in self.blocked_nodes:
            self.unknown_nodes.add(node)
            self.updated_at = time.time()

    def mark_danger(self, coord: Iterable[int] | Coord, amount: int = 1) -> None:
        node = as_coord(coord)
        self.danger_nodes[node] = self.danger_nodes.get(node, 0) + max(1, int(amount))
        self.updated_at = time.time()

    def record_edge(self, before: Iterable[int], waypoint: Iterable[int], *, success: bool) -> None:
        key = f"{coord_key(as_coord(before))}->{coord_key(as_coord(waypoint))}"
        target = self.edge_successes if success else self.edge_failures
        target[key] = target.get(key, 0) + 1
        self.updated_at = time.time()

    def record_movement(
        self,
        before: Iterable[int],
        after: Iterable[int],
        *,
        success: bool,
        stuck: bool,
        waypoint: Iterable[int] | None = None,
    ) -> None:
        before_node = as_coord(before)
        after_node = as_coord(after)
        self.mark_walkable(before_node)
        if success and after_node != before_node:
            for node in interpolate_coords(before_node, after_node):
                self.mark_walkable(node)
        if waypoint is not None:
            self.record_edge(before_node, waypoint, success=success and not stuck)
            if stuck:
                self.mark_danger(waypoint)

    def stuck_areas(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = sorted(self.danger_nodes.items(), key=lambda item: item[1], reverse=True)
        return [{"coord": [coord[0], coord[1]], "stuck_count": count} for coord, count in rows[:limit]]
