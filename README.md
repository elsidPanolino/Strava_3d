# Strava 3D

Turn your Strava activities into **3D-printable terrain models**. The tool connects to
your Strava account through the official API, downloads the GPS track of an activity,
gets elevation data for the surrounding area, and writes watertight STL files plus a
**Bambu Studio-ready 3MF**: a terrain tile with your route raised on it (or cut into it).

Tuned for the **Bambu Lab A1 mini** by default (180 mm bed, 0.4 nozzle, AMS lite),
with profiles for other common printers.

**Features**
- Built for cycling: rides-first activity list with time and speed, full ride stats (power,
  heart rate, kudos, PRs…), your Strava totals, and ride stats on the name plate
- Islands and coasts (e.g. Palawan): the sea is flattened at the base and the land lifted, so
  coastlines and islands read clearly and the sea can print blue with one filament change
- Auto vertical exaggeration sizes the hills to the model, so long rides at small scale still have relief
- Printer profiles that size the model to the bed, warn when it won't fit, snap heights to
  whole layers and keep the route at least 3 nozzle widths wide
- Rectangle, square, circle or hexagon shapes
- Name plate with the activity name, distance, elevation gain and date
- Colour bands by height: exact filament-change layers for the A1 mini (AMS lite, or manual swaps)
- Route as a separate part already assigned to filament 2 in the 3MF, for AMS lite
- Step-by-step printing guide with Bambu Studio presets, plus a filament estimate
- Web UI with a 3D preview on your printer's bed, and a full command-line interface

![Strava 3D web interface](docs/ui.png)

## How it works

1. **Strava API**: OAuth login, then the activity's full-resolution `latlng` stream.
2. **Elevation**: free global [AWS Terrain Tiles](https://registry.opendata.aws/terrain-tiles/)
   (Terrarium PNG encoding, no API key needed; up to about 5 m/pixel). Tiles are cached in `~/.cache/strava3d`.
3. **Mesh**: the terrain becomes a closed heightmap solid with a flat base. The route
   is drawn into it as a raised ridge or a groove, or exported as a separate body for
   multi-colour printing.

## Setup

```bash
git clone https://github.com/elsidpanolino/strava_3d.git
cd strava_3d
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

### Create a Strava API application

1. Go to <https://www.strava.com/settings/api> and create an app.
2. Set **Authorization Callback Domain** to `localhost`.
3. Copy `.env.example` to `.env` and fill in your **Client ID** and **Client Secret**.

## Web interface (easiest)

```bash
strava3d ui
```

This opens <http://localhost:8765> in your browser. There you can:

1. **Set up**: paste your Strava Client ID and Client Secret (first run only; they're saved in
   `~/.config/strava3d/credentials.json`). If you prefer, put them in `.env` instead.
2. **Connect with Strava**: log in once. Disconnect from the top-right corner.
3. **Choose a route**: pick an activity from your list, which opens on **Rides** with distance,
   time and speed, or drop in a GPX file. The **Your Strava stats** card shows your ride or run
   totals for the last 4 weeks, this year and all time, plus your longest ride and biggest climb.
4. **Pick your printer**: the A1 mini is the default. Size limits and layer heights follow the printer.
5. **Tune it**: shape, size, vertical exaggeration, route style and name plate.
   Under **Colours**, choose height bands, a base colour or a separate route part, and pick
   each colour with the swatches. The 3D preview updates live.
6. **Build**: rotate and zoom the result on the printer's bed, check the fit and filament
   estimate, see the activity's full stats, download the `.3mf` (or STLs), and follow the
   **How to print** steps.

The viewer is plain WebGL with no external scripts, so the UI also works offline
once elevation tiles are cached.

## Command line

### Connect your account

```bash
strava3d auth
```

A browser window opens. When you approve access, Strava redirects to
`http://localhost:8765/callback` and the token is saved to
`~/.config/strava3d/token.json`. Tokens are refreshed automatically after that.

## Usage

```bash
strava3d activities --rides         # list recent rides with their IDs, time and speed
strava3d stats                      # your ride totals (--sport run|swim for others)
strava3d build 1234567890           # build a model from one activity
strava3d latest                     # build from your most recent GPS activity
strava3d gpx examples/tamalpais-loop.gpx   # build from a GPX file (no Strava needed)
```

Output goes to `./output/`:

- `<name>.3mf`: the whole model for Bambu Studio / OrcaSlicer, centred on the bed, with
  the route part set to filament 2 when `--separate-route` is used
- `<name>-terrain.stl`: the terrain tile
- `<name>-route.stl`: the route as its own body (only with `--separate-route`)
- `<name>-preview.png`: a shaded top-down preview

The command also prints a short guide, including the layers for any filament changes.
`strava3d printers` lists the printer profiles.

### Model options

| Option | Default | Description |
|---|---|---|
| `--printer` | a1mini | Printer profile: `a1mini`, `a1`, `p1s`, `h2d`, `mk4`, `coreone`, `mini`, `ender3`, `k1` |
| `--layer` | 0.2 | Layer height. Base, route and text heights snap to whole layers. |
| `--size` | fit printer | Longest side in mm (A1 mini: 170). Capped at 200 by default. |
| `--shape` | rectangle | `rectangle`, `square`, `circle`, `hexagon` |
| `--exaggeration` | auto | Vertical exaggeration, or `auto` to make the relief about 12% of the model size |
| `--water` | auto | `auto`/`on`/`off`: flatten the sea at the base and lift the land |
| `--sea-color` | auto | Sea in its own colour when there is sea (`on`/`off` to force) |
| `--coast-step` | 1.0 | Height of the land's edge above the flat sea, in mm |
| `--route` | raised | `raised`, `groove`, or `none` |
| `--route-width` | 1.6 | Route line width in mm (at least 3× the nozzle) |
| `--route-height` | 1.0 | How far the route is raised or grooved, in mm |
| `--separate-route` | off | Route as its own part / STL (filament 2 in the 3MF) |
| `--label` | off | Name plate with name, distance, elevation gain and date (rectangle/square) |
| `--label-text` | activity name | Custom name plate title |
| `--label-stats` | distance,elevation,time,date | Name plate stats from `distance,elevation,time,speed,date` |
| `--colors` | 1 | 2–4 colour bands by height, using filament changes |
| `--base-color` | off | Base in its own colour (one more filament change) |
| `--base` | 3 | Base thickness under the lowest point, in mm |
| `--padding` | 0.1 | Terrain margin around the route (fraction of route size) |
| `--resolution` | 0.5 | Mesh cell size in mm (smaller gives more detail and bigger files) |
| `-o / --out` | output | Output directory |

### Examples

```bash
# Palawan ride: blue sea, green-brown-white land, ride stats on the plate, square tile
strava3d build 1234567890 --square --colors 3 --label --label-stats distance,elevation,time,speed,date

# Try it without Strava, using the included example ride
strava3d gpx examples/palawan-puerto-princesa-sabang.gpx --square --colors 3 --label

# A1 mini, 3 colours by height on a dark base, with a name plate
strava3d build 1234567890 --colors 3 --base-color --label

# Hexagon tile with the route on AMS lite slot 2
strava3d build 1234567890 --shape hexagon --separate-route

# Flat road ride: exaggerate the relief more
strava3d build 1234567890 --exaggeration 4

# Bigger print on an A1
strava3d build 1234567890 --printer a1 --size 240 --shape circle
```

## Coasts and islands (Palawan)

Elevation tiles include the sea floor (down to about −580 m around Palawan). That would
make the sea look like deep terrain and squash the island. Instead, when at least 2% of the
area is sea, strava3d separates sea from land clearly:

- **Flat sea**: everything below the coastline becomes a flat surface on top of the base.
- **Sharp shoreline**: the land rises in a 1.0 mm (5-layer) step at the coast (`--coast-step`).
  Land/sea is measured on a 4× finer grid and averaged, so the edge follows the real
  shoreline and is never more than one mesh cell wide.
- **Clean coastline data**: the shoreline is traced at 1 m, not 0 m, because the elevation
  tiles blend coarse bathymetry into a blocky 0–1 m fringe around every coast. At 1 m,
  islands and bays keep their natural shapes.
- **Blue sea by default**: one filament change at the top of the base, so the sea prints in its
  own colour and every coastline is a clean colour edge (`--sea-color off` to disable).

Long point-to-point rides (for example, Puerto Princesa → Sabang) come out long and thin.
Pick the **Square** shape to show more of the island at the same scale and use the whole
A1 mini bed. Auto exaggeration (about 5× for that ride) keeps the mountains visible at
1:400,000 scale.

## Printing tips (Bambu Lab A1 mini)

- Open the `.3mf` in Bambu Studio. Choose the **Bambu Lab A1 mini 0.4 nozzle** printer and the
  **0.20mm Standard @BBL A1M** process (or 0.12mm Fine for more detail). Print flat, without supports.
- 2 walls and 10–15% gyroid infill is plenty. A 170 mm tile uses roughly 120–200 g of PLA.
- **Colour bands**: in Preview, go to each layer the guide lists, right-click the **+** on the
  layer slider, and choose **Add color change** (AMS lite) or **Add pause** (swap filament by hand).
- **Separate route**: the route part is already on filament 2. Load the route colour in AMS lite slot 2.
- **Name plate**: the text is raised 0.8 mm (4 layers). Turn on `--base-color` to print it in a
  contrasting colour.
- Other printers: import the STLs. If you use a separate route, load the terrain and route STLs together
  as one object with two parts, and give the route its own filament.

## Development

```bash
pip install -e ".[test]"
pytest
```

## Notes

- Strava's API terms allow use with your own data. Rate limits are 100 requests per 15 min
  and 1,000 per day, and one model uses about 3 requests.
- Elevation data © Mapzen / AWS Terrain Tiles, which include SRTM, GMTED, NED, ETOPO1 and other sources.
  See [attribution](https://github.com/tilezen/joerd/blob/master/docs/attribution.md).
- `examples/tamalpais-loop.gpx` is a synthetic loop on real terrain, used for testing.
  `examples/palawan-puerto-princesa-sabang.gpx` is an approximate Puerto Princesa → Sabang
  ride (waypoints along the highway with made-up timing), not a recorded activity.
- The 3MF has not yet been tested in every slicer version. If parts or filaments don't come through,
  use the STL files.
