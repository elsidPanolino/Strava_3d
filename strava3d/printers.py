"""3D printer profiles: build volume and nozzle, used to size and sanity-check models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Printer:
    key: str
    name: str
    bed_x: float
    bed_y: float
    bed_z: float
    nozzle: float = 0.4
    layer: float = 0.2  # default layer height for the profile
    first_layer: float = 0.2
    ams: str = ""  # multi-material unit name, if the printer has one
    margin: float = 5.0  # keep this far from the bed edges

    @property
    def max_size(self) -> float:
        """Longest side that fits a square-ish model on the bed with margin."""
        return min(self.bed_x, self.bed_y) - 2 * self.margin

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "bed": [self.bed_x, self.bed_y, self.bed_z],
            "nozzle": self.nozzle,
            "layer": self.layer,
            "ams": self.ams,
            "max_size": self.max_size,
        }


PRINTERS: dict[str, Printer] = {
    p.key: p
    for p in [
        Printer("a1mini", "Bambu Lab A1 mini", 180, 180, 180, ams="AMS lite"),
        Printer("a1", "Bambu Lab A1", 256, 256, 256, ams="AMS lite"),
        Printer("p1s", "Bambu Lab P1S / P1P / X1C", 256, 256, 256, ams="AMS"),
        Printer("h2d", "Bambu Lab H2D", 325, 320, 325, ams="AMS"),
        Printer("mk4", "Prusa MK4 / MK4S", 250, 210, 220, ams="MMU3"),
        Printer("coreone", "Prusa Core One", 250, 220, 270, ams="MMU3"),
        Printer("mini", "Prusa MINI+", 180, 180, 180),
        Printer("ender3", "Creality Ender-3 (V2/V3)", 220, 220, 250),
        Printer("k1", "Creality K1 / K1C", 220, 220, 250, ams="CFS"),
    ]
}
DEFAULT_PRINTER = "a1mini"


def snap(value: float, step: float, minimum: float | None = None) -> float:
    """Round to a whole number of layers (at least one layer, or `minimum`)."""
    n = max(1, round(value / step))
    out = round(n * step, 4)
    return max(out, minimum) if minimum is not None else out


def layer_number(z: float, printer: Printer, layer: float | None = None) -> int:
    """1-based index of the layer whose top is at height z."""
    layer = layer or printer.layer
    return max(1, int(round((z - printer.first_layer) / layer)) + 1)
