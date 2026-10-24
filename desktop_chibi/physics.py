"""Deterministic desktop-floor physics, independent of Qt."""
from dataclasses import dataclass


@dataclass
class Motion:
    x: float = 0.0
    y: float = 0.0
    vy: float = 0.0
    airborne: bool = False

    def step(self, dt, left, right, top, floor, gravity, enabled=True):
        """Integrate gravity and clamp to the workspace. Return True on impact."""
        self.x = max(left, min(self.x, right))
        self.y = max(top, self.y)
        if not enabled:
            self.y, self.vy, self.airborne = floor, 0.0, False
            return False
        if self.y >= floor:
            landed = self.airborne
            self.y, self.vy, self.airborne = floor, 0.0, False
            return landed
        self.airborne = True
        self.y += self.vy * dt + 0.5 * gravity * dt * dt
        self.vy += gravity * dt
        if self.y >= floor:
            self.y, self.vy, self.airborne = floor, 0.0, False
            return True
        return False
