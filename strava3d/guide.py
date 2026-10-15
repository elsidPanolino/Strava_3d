"""Printer-specific, step-by-step printing instructions for a built model."""

from __future__ import annotations

BAMBU_PROCESS = {
    # (printer key prefix, layer) -> Bambu Studio process preset name
    ("a1mini", 0.2): "0.20mm Standard @BBL A1M",
    ("a1mini", 0.12): "0.12mm Fine @BBL A1M",
    ("a1mini", 0.16): "0.16mm Optimal @BBL A1M",
    ("a1mini", 0.08): "0.08mm Extra Fine @BBL A1M",
    ("a1", 0.2): "0.20mm Standard @BBL A1",
    ("a1", 0.12): "0.12mm Fine @BBL A1",
    ("a1", 0.16): "0.16mm Optimal @BBL A1",
}


def print_guide(info: dict) -> list[str]:
    printer = info["printer"]
    key, layer = printer["key"], info["layer_mm"]
    bambu = printer["name"].startswith("Bambu")
    slicer = "Bambu Studio" if bambu else "your slicer (PrusaSlicer / OrcaSlicer / Cura)"
    multi = info.get("route_style") == "separate"
    steps = []

    if bambu:
        preset = BAMBU_PROCESS.get((key, round(layer, 2)), f"{layer:.2f}mm @BBL")
        steps.append(
            f"Open the .3mf in Bambu Studio and pick printer \"{printer['name']}\" (0.4 nozzle) "
            f"with process \"{preset}\"."
        )
    else:
        steps.append(
            f"Import the terrain STL into {slicer}"
            + (" together with the route STL, as one object with two parts." if multi else ".")
            + f" Use {layer:.2f} mm layers."
        )
    steps.append("Print flat on its base. No supports or brim needed. 2 walls and 10–15% gyroid infill is plenty.")

    if multi:
        if bambu:
            ams = printer["ams"] or "AMS"
            steps.append(
                f"The route is already set to filament 2. Load your route colour in {ams} slot 2 "
                "and the terrain colour in slot 1."
            )
        else:
            steps.append("Assign the route part to your second filament/extruder.")

    bands = info.get("bands") or []
    if bands:
        how = (
            "In Preview, drag the layer slider to the layer, right-click the + and choose "
            + ("\"Add color change\" (AMS lite switches automatically) or \"Add pause\" to swap filament by hand."
               if bambu else "\"Add color change\" (M600).")
        )
        steps.append(f"Colour bands: add a filament change at each layer below. {how}")
        for i, b in enumerate(bands, start=2):
            steps.append(f"    Colour {i}: from layer {b['layer']} (Z {b['top_z']:.2f} mm)")

    if info.get("label_mm") and not bands:
        steps.append(
            "Tip: turn on \"Base in its own colour\" to print the name plate text in a contrasting colour."
        )
    steps.append(f"Estimated filament: about {info['filament_g']} g PLA.")
    return steps
