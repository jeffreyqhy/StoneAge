from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable, Iterable

from .coordinate_mapper import ClickPlan, CoordinateMapper


Coord = tuple[int, int]


@dataclass(frozen=True)
class CalibrationAction:
    angle: float
    radius: int
    click: ClickPlan


class MovementCalibrator:
    def __init__(self, mapper: CoordinateMapper | None = None) -> None:
        self.mapper = mapper or CoordinateMapper()

    def build_actions(
        self,
        *,
        character_position: Iterable[int],
        screen_size: Iterable[int],
        radii: Iterable[int] = (240, 300, 360, 430),
        directions: int = 8,
    ) -> list[CalibrationAction]:
        actions: list[CalibrationAction] = []
        origin = (0, 0)
        for index in range(max(4, int(directions))):
            angle = math.tau * index / max(4, int(directions))
            waypoint = (int(round(math.cos(angle) * 5)), int(round(math.sin(angle) * 5)))
            for radius in radii:
                click = self.mapper.click_for_waypoint(
                    origin,
                    waypoint,
                    character_position=character_position,
                    radius=int(radius),
                    screen_size=screen_size,
                )
                actions.append(CalibrationAction(angle=angle, radius=int(radius), click=click))
        return actions

    def run(
        self,
        *,
        map_id: str,
        read_coord: Callable[[], Coord | None],
        tap: Callable[[int, int], None],
        save_sample: Callable[..., bool],
        character_position: Iterable[int],
        screen_size: Iterable[int],
        radii: Iterable[int] = (240, 300, 360, 430),
        directions: int = 8,
        settle_seconds: float = 0.8,
    ) -> int:
        saved = 0
        for action in self.build_actions(
            character_position=character_position,
            screen_size=screen_size,
            radii=radii,
            directions=directions,
        ):
            before = read_coord()
            if before is None:
                continue
            started = time.monotonic()
            tap(action.click.point[0], action.click.point[1])
            time.sleep(max(0.1, float(settle_seconds)))
            after = read_coord()
            if after is None:
                continue
            delta = [int(after[0]) - int(before[0]), int(after[1]) - int(before[1])]
            if save_sample(
                map_id=map_id,
                before_game_coord=list(before),
                after_game_coord=list(after),
                click_relative_to_character=list(action.click.relative_to_character),
                click_angle=action.click.angle,
                click_radius=action.click.radius,
                actual_delta=delta,
                duration=time.monotonic() - started,
                success=before != after,
                stuck=before == after,
                progress_score=0.0,
                direction=action.click.direction,
                strategy="calibration",
            ):
                saved += 1
        return saved

