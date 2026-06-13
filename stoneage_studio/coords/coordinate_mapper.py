from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable


Point = tuple[int, int]
SafeRect = tuple[int, int, int, int]


@dataclass(frozen=True)
class ClickPlan:
    point: Point
    relative_to_character: Point
    angle: float
    radius: int
    direction: str


def clamp(value: float, low: int, high: int) -> int:
    return max(low, min(high, int(round(value))))


class CoordinateMapper:
    DIRECTIONS_8 = (
        "E",
        "SE",
        "S",
        "SW",
        "W",
        "NW",
        "N",
        "NE",
    )

    def estimate_character_screen_position(
        self,
        screen_size: Iterable[int],
        override: Iterable[int] | None = None,
    ) -> Point:
        if override is not None:
            ox, oy = override
            return int(ox), int(oy)
        width, height = screen_size
        return int(width) // 2, int(round(int(height) * 0.52))

    def angle_for_game_delta(self, current: Iterable[int], waypoint: Iterable[int]) -> float:
        current_x, current_y = current
        waypoint_x, waypoint_y = waypoint
        dx = int(waypoint_x) - int(current_x)
        dy = int(waypoint_y) - int(current_y)
        if dx == 0 and dy == 0:
            return math.pi / 2
        # StoneAge uses an isometric-looking map: screen-right tends to move
        # both game axes forward, while screen-up increases X and decreases Y.
        # Convert game-coordinate delta to screen click direction before
        # choosing an angle.
        screen_dx = dx + dy
        screen_dy = dy - dx
        if screen_dx == 0 and screen_dy == 0:
            return math.pi / 2
        return math.atan2(screen_dy, screen_dx)

    def direction_for_angle(self, angle: float, *, buckets: int = 8) -> str:
        if buckets != 8:
            step = 360.0 / max(1, int(buckets))
            degrees = (math.degrees(angle) + 360.0) % 360.0
            return f"{int(round(degrees / step)) % int(buckets):02d}"
        degrees = (math.degrees(angle) + 360.0) % 360.0
        index = int((degrees + 22.5) // 45.0) % 8
        return self.DIRECTIONS_8[index]

    def default_movement_safe_rect(self, screen_size: Iterable[int]) -> SafeRect:
        width, height = screen_size
        width = int(width)
        height = int(height)
        return (
            int(round(width * 0.22)),
            int(round(height * 0.16)),
            int(round(width * 0.84)),
            int(round(height * 0.79)),
        )

    def normalize_safe_rect(
        self,
        screen_size: Iterable[int],
        safe_rect: Iterable[int] | None = None,
    ) -> SafeRect:
        width, height = screen_size
        width = int(width)
        height = int(height)
        if safe_rect is None:
            left, top, right, bottom = self.default_movement_safe_rect((width, height))
        else:
            values = [int(value) for value in safe_rect]
            if len(values) < 4:
                left, top, right, bottom = self.default_movement_safe_rect((width, height))
            else:
                left, top, third, fourth = values[:4]
                if third > left and fourth > top:
                    right, bottom = third, fourth
                else:
                    right, bottom = left + max(1, third), top + max(1, fourth)
        left = clamp(left, 0, max(0, width - 1))
        top = clamp(top, 0, max(0, height - 1))
        right = clamp(right, left + 1, max(left + 1, width - 1))
        bottom = clamp(bottom, top + 1, max(top + 1, height - 1))
        return left, top, right, bottom

    def safe_radius_for_angle(
        self,
        character_position: Iterable[int],
        angle: float,
        requested_radius: int,
        safe_rect: SafeRect,
    ) -> int:
        character_x, character_y = character_position
        left, top, right, bottom = safe_rect
        cos_value = math.cos(angle)
        sin_value = math.sin(angle)
        limits = [float(max(1, int(requested_radius)))]
        if cos_value > 0:
            limits.append((right - int(character_x)) / cos_value)
        elif cos_value < 0:
            limits.append((left - int(character_x)) / cos_value)
        if sin_value > 0:
            limits.append((bottom - int(character_y)) / sin_value)
        elif sin_value < 0:
            limits.append((top - int(character_y)) / sin_value)
        positive_limits = [value for value in limits if value > 0]
        if not positive_limits:
            return max(1, int(requested_radius))
        return max(1, int(round(min(positive_limits))))

    def click_for_waypoint(
        self,
        current: Iterable[int],
        waypoint: Iterable[int],
        *,
        character_position: Iterable[int],
        radius: int,
        screen_size: Iterable[int],
        safe_rect: Iterable[int] | None = None,
    ) -> ClickPlan:
        width, height = screen_size
        character_x, character_y = character_position
        angle = self.angle_for_game_delta(current, waypoint)
        safe_bounds = self.normalize_safe_rect((width, height), safe_rect)
        safe_radius = self.safe_radius_for_angle(
            character_position,
            angle,
            int(radius),
            safe_bounds,
        )
        relative_x = int(round(math.cos(angle) * safe_radius))
        relative_y = int(round(math.sin(angle) * safe_radius))
        left, top, right, bottom = safe_bounds
        point = (
            clamp(int(character_x) + relative_x, left, right),
            clamp(int(character_y) + relative_y, top, bottom),
        )
        return ClickPlan(
            point=point,
            relative_to_character=(point[0] - int(character_x), point[1] - int(character_y)),
            angle=angle,
            radius=safe_radius,
            direction=self.direction_for_angle(angle),
        )

    def click_for_game_coord_direct(
        self,
        current: Iterable[int],
        target: Iterable[int],
        *,
        character_position: Iterable[int],
        screen_size: Iterable[int],
        tile_radius: int = 90,
        min_radius: int = 170,
        max_radius: int = 300,
        safe_rect: Iterable[int] | None = None,
    ) -> ClickPlan:
        """Project a nearby game coordinate to a click point around the character.

        This is for exact final taps. It deliberately keeps the click away from
        the character body so one-tile corrections do not open the character
        target picker.
        """
        width, height = screen_size
        character_x, character_y = character_position
        current_x, current_y = current
        target_x, target_y = target
        dx = int(target_x) - int(current_x)
        dy = int(target_y) - int(current_y)
        screen_dx = dx + dy
        screen_dy = dy - dx
        if screen_dx == 0 and screen_dy == 0:
            screen_dy = 1
        tile_x = max(12, int(tile_radius))
        tile_y = max(8, int(round(tile_x * 0.56)))
        relative_x = float(screen_dx * tile_x)
        relative_y = float(screen_dy * tile_y)
        radius = math.hypot(relative_x, relative_y)
        if radius <= 0:
            radius = 1.0
        lower = max(1, int(min_radius))
        upper = max(lower, int(max_radius))
        if radius < lower:
            scale = lower / radius
            relative_x *= scale
            relative_y *= scale
            radius = float(lower)
        elif radius > upper:
            scale = upper / radius
            relative_x *= scale
            relative_y *= scale
            radius = float(upper)
        angle = math.atan2(relative_y, relative_x)
        safe_bounds = self.normalize_safe_rect((width, height), safe_rect)
        left, top, right, bottom = safe_bounds
        point = (
            clamp(int(character_x) + relative_x, left, right),
            clamp(int(character_y) + relative_y, top, bottom),
        )
        actual_relative = (point[0] - int(character_x), point[1] - int(character_y))
        return ClickPlan(
            point=point,
            relative_to_character=actual_relative,
            angle=angle,
            radius=int(round(math.hypot(actual_relative[0], actual_relative[1]))),
            direction=self.direction_for_angle(angle),
        )
