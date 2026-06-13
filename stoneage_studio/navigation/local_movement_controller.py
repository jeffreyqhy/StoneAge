from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ..coords.coordinate_mapper import ClickPlan, CoordinateMapper


@dataclass(frozen=True)
class MovementClick:
    point: tuple[int, int]
    relative_to_character: tuple[int, int]
    angle: float
    radius: int
    direction: str
    score: float
    strategy_key: str
    source: str


class LocalMovementController:
    DEFAULT_RADII = (180, 220, 260, 300)

    def __init__(self, mapper: CoordinateMapper | None = None) -> None:
        self.mapper = mapper or CoordinateMapper()

    def choose_click(
        self,
        current: Iterable[int],
        waypoint: Iterable[int],
        *,
        screen_size: Iterable[int],
        profile: dict[str, Any] | None = None,
        character_position: Iterable[int] | None = None,
        configured_radii: Iterable[int] | None = None,
        blocked_strategies: set[str] | None = None,
        safe_rect: Iterable[int] | None = None,
    ) -> MovementClick:
        profile = profile or {}
        blocked_strategies = blocked_strategies or set()
        character = self.mapper.estimate_character_screen_position(screen_size, character_position)
        radii = self._candidate_radii(profile, configured_radii)
        plans: list[tuple[float, ClickPlan, str]] = []
        for radius in radii:
            plan = self.mapper.click_for_waypoint(
                current,
                waypoint,
                character_position=character,
                radius=radius,
                screen_size=screen_size,
                safe_rect=safe_rect,
            )
            strategy_key = f"{plan.direction}:{plan.radius}"
            if strategy_key in blocked_strategies:
                continue
            score = self._score(plan, profile)
            plans.append((score, plan, strategy_key))
        if not plans:
            plan = self.mapper.click_for_waypoint(
                current,
                waypoint,
                character_position=character,
                radius=self.DEFAULT_RADII[1],
                screen_size=screen_size,
                safe_rect=safe_rect,
            )
            return MovementClick(
                point=plan.point,
                relative_to_character=plan.relative_to_character,
                angle=plan.angle,
                radius=plan.radius,
                direction=plan.direction,
                score=0.0,
                strategy_key=f"{plan.direction}:{plan.radius}",
                source="fallback",
            )
        score, best, strategy_key = max(plans, key=lambda item: item[0])
        return MovementClick(
            point=best.point,
            relative_to_character=best.relative_to_character,
            angle=best.angle,
            radius=best.radius,
            direction=best.direction,
            score=score,
            strategy_key=strategy_key,
            source="profile" if profile else "default",
        )

    def _candidate_radii(self, profile: dict[str, Any], configured: Iterable[int] | None) -> list[int]:
        radii: list[int] = []
        best_radius = profile.get("map_best_radius")
        if best_radius and not configured:
            radii.append(int(best_radius))
            radii.extend([int(best_radius) - 40, int(best_radius) + 40])
        if configured:
            radii.extend(int(value) for value in configured)
        else:
            radii.extend(self.DEFAULT_RADII)
        cleaned = sorted({radius for radius in radii if 60 <= radius <= 340})
        return cleaned or list(self.DEFAULT_RADII)

    def _score(self, plan: ClickPlan, profile: dict[str, Any]) -> float:
        direction_rate = float((profile.get("direction_success_rates") or {}).get(plan.direction, 0.55))
        radius_rate = float((profile.get("radius_success_rates") or {}).get(str(plan.radius), 0.55))
        angle_rate = float((profile.get("approach_angle_success") or {}).get(plan.direction, direction_rate))
        best_radius = int(profile.get("map_best_radius") or plan.radius)
        radius_penalty = min(abs(plan.radius - best_radius) / 500.0, 0.35)
        return direction_rate * 0.42 + radius_rate * 0.36 + angle_rate * 0.22 - radius_penalty
