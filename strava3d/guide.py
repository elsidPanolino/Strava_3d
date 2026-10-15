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
                f"The route is already set to filament {info.get('route_filament', 2)}. Load your route "
                f"colour in {ams} slot {info.get('route_filament', 2)}"
                + (f" and the sea/land colours in slots 1–{info['route_filament'] - 1}."
                   if info.get("route_filament", 2) > 2 else " and the terrain colour in slot 1.")
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
        if info.get("route_style") in ("raised", "groove"):
            steps.append(
                "With colour bands the route prints in the same colours as the land around it. To make it "
                "stand out, use \"Route as a separate part\" with AMS lite, or paint it afterwards."
            )

    if info.get("single_color"):
        steps.append(
            "One colour: load a single spool and print as is. Don't add any colour changes. "
            + ("The flat sea and the step at every coastline still show the shoreline. " if info.get("water") else "")
            + "The raised route and name plate show up through light and shadow."
        )
    elif info.get("sea_color"):
        style = info.get("sea_style", "flat")
        surface = {"flat": "flat sea", "waves": "waves", "depth": "sea"}[style]
        if info.get("shallow_color"):
            steps.append(
                f"Colour 1 is the deep sea (and the base under it). Colour 2, from layer {bands[0]['layer']}, "
                "is shallow water: reefs and lagoons sit higher, so they print in this colour. "
                f"Colour 3, from layer {bands[1]['layer']}, is the land, which rises "
                f"{info['coast_step_mm']:.1f} mm above the sea at every coastline."
            )
        else:
            steps.append(
                f"Colour 1 is the sea (and the base under it). Colour 2 starts at layer {bands[0]['layer']}: "
                f"the land rises {info['coast_step_mm']:.1f} mm above the {surface}, so every coastline is a "
                "sharp step and a clean colour edge."
            )
    elif info.get("water"):
        steps.append(
            "Tip: this area has sea. Turn on \"Sea in its own colour\" (--sea-color on) to print it blue "
            "with one filament change at the coastline."
        )
    if info.get("water") and info.get("sea_style") == "waves":
        steps.append("The wave texture is only a couple of layers deep. 0.12–0.16 mm layers show it best.")
    elif (info.get("water") and info.get("sea_style") == "depth" and not info.get("shallow_color")
          and info.get("sea_color")):
        steps.append("Depth sea: reefs and shallows sit higher than deep water. Turn on "
                     "\"Shallow water colour\" to print them turquoise over a deep blue.")
    if info.get("label_mm") and not bands and not info.get("single_color"):
        steps.append(
            "Tip: turn on \"Base in its own colour\" to print the name plate text in a contrasting colour."
        )
    steps.append(f"Estimated filament: about {info['filament_g']} g PLA.")
    return steps
