# Anomaly Tools — QGIS Plugin User Guide

## 1. What this plugin does

**Anomaly Tools** is a QGIS QC (quality control) utility for refining the output of a deep‑learning anomaly detector. It assumes your detector has produced a **vector layer of candidate anomaly polygons** (e.g. bounding boxes or footprints), and that you also have a **raster layer** covering the same area to check each candidate against.

The plugin does two things:

1. **Compute Stats** — derives simple geometric descriptors (length, width, area, elongation ratio) for each candidate polygon, samples a raster statistic of your choice inside each polygon, and reports summary statistics for the whole set.
2. **Filter Features** — lets you set thresholds on those descriptors and extracts only the candidates that pass, into a new "cleaned" layer.

In short: it turns "the model flagged 500 blobs" into "here are the 40 blobs that actually look like the geometric shape you expect, with a raster reading in the expected range."

### Worked example used throughout this guide: DEM-based depth anomalies

Say your detector flags candidate depressions/sinkholes on a DEM, and you want to keep only candidates that are (a) roughly the right shape/size, and (b) actually deep enough to be a real feature rather than noise. Here:

- **Vector layer** = candidate depression footprints (rectangular boxes from the detector).
- **Raster layer** = the DEM.
- **Raster statistic** = **Min**, so each polygon gets the lowest elevation found inside it — i.e. its deepest point.
- You'd then filter on that field being *below* a threshold elevation (e.g. keep only candidates whose minimum elevation is at least 2 m lower than the surrounding surface).

The plugin isn't limited to this case — the raster can be a DEM, a spectral index, a model confidence surface, or anything else, and you choose which statistic to compute — but this DEM/depth scenario is used below as a concrete running example.

## 2. Requirements / inputs

| Input | What it should be |
|---|---|
| **Vector layer** | Polygon layer of candidate anomalies. **Each polygon must be a simple 4‑sided shape (a closed ring with exactly 5 points: 4 corners + the repeated closing point)** — e.g. rectangular bounding boxes exported from a detector. Polygons that aren't 4‑sided will be skipped (their geometric fields are set to 0 — see §5). |
| **Raster layer** | **Any raster** covering the same area as the vector layer — a DEM, spectral band, index, or confidence surface. Any band can be used, and it's selected in the dialog (see §4). |

Both layers must already be loaded in the QGIS project before opening the plugin — use the **Refresh layers** button if you load a layer after the dialog is already open.

## 3. Opening the plugin

Go to **Plugins → AnomalyTools → Anomaly Tools**, or click its icon in the toolbar. The dialog has two tabs: **Compute Stats** and **Filter Features**.

## 4. Tab 1 — Compute Stats

### Steps

1. **Input vector layer**: choose your candidate anomaly polygons.
2. **Input raster layer**: choose the raster to sample under each polygon (e.g. your DEM).
3. **Raster band**: pick which band of that raster to use. The spinbox range updates automatically to the selected raster's band count (for a single-band DEM this will just be band 1).
4. **Raster statistic per polygon**: choose which per-polygon statistic to compute — Mean, Median, St dev, Min, Max, or Range.
   - For the DEM/depth example: choose **Min** to get each candidate's deepest point, or **Range** to get local relief (max − min elevation) under the footprint.
5. Click **Run: Compute Stats + Zonal Stats**.
6. A popup shows summary statistics (min / max / mean / std. dev.) across all features.
7. Optionally click **Save Report as TXT** to write that summary to a text file.

### What happens when you click Run

- A **copy** of your input vector layer is created and added to the map, named `<original_name>_stat`. This is the working layer — your original input is left untouched.
- Four geometric fields are added/recomputed on every feature:

| Field | Meaning | How it's computed |
|---|---|---|
| **Length** | The longer of the two edges emanating from the polygon's second vertex | The larger of the distances between vertex 1↔2 and vertex 2↔3 |
| **Width** | The shorter of those two edges | The smaller of the distances between vertex 1↔2 and vertex 2↔3 |
| **Area** | A rectangle-based area estimate | `Length × Width` — **note:** this is *not* the true polygon area (`$area`); it's the product of the two edge lengths, which only equals the true area if the shape is a true rectangle. Use it as a consistent QC metric, not as a survey-grade area. |
| **Ratio** | Elongation / aspect ratio | `Length / Width` (0 if Width is 0). A value near 1 means roughly square; larger values mean more elongated shapes. |

  These four fields are recomputed **every time you click Run**, overwriting previous values on the `_stat` layer.

- Any pre-existing zonal-statistics fields (names starting with `raster_` or `zs_`) are deleted first, then **zonal statistics** are re-run against your chosen raster, band, and statistic.
- **The resulting field name is detected automatically** rather than assumed. QGIS/OGR can rename or truncate the output field depending on format (e.g. a shapefile's 10-character field-name limit turns `raster_range` into `raster_ran`), so the plugin compares the field list before and after running zonal statistics to find out exactly what was added, and uses that field from then on. Tab 2's labels update to show you the exact field name in use, e.g. `Raster Min [raster_min] (band 1) (min):`.
- Finally, the plugin computes **min / max / mean / population std. dev.** of `Length`, `Width`, `Area`, `Ratio`, and the raster statistic field across *all* features in the `_stat` layer, shows them in a popup, and keeps them in memory so you can export them with **Save Report as TXT**.

**Tip:** running this twice on the same input layer name will create a second `_stat` layer with the same name (QGIS allows duplicate layer display names), which can be confusing — rename or remove the old one first if you want to avoid duplicates.

## 5. Tab 2 — Filter Features

This tab thresholds the fields created in Tab 1, so **run Tab 1 first** and select its output (`<name>_stat`) as the input here.

### Parameters

| Parameter | Meaning | Default |
|---|---|---|
| **Area (min / max)** | Keep only features whose `Area` (Length × Width) falls within this range. | 1.5 – 2.0 |
| **Ratio (min / max)** | Keep only features whose elongation `Ratio` (Length/Width) falls within this range. | 1.1 – 1.6 |
| **Raster stat (min / max)** | Keep only features whose selected raster statistic (whatever you chose in Tab 1 — e.g. minimum elevation) falls within this range. The row label shows exactly which field this is filtering on. | min: −1×10⁹ (effectively no lower bound), max: 0.5 |

For the DEM/depth example, if you computed **Min elevation** in Tab 1 and want to keep only candidates that dip at least 2 m below a local baseline of, say, 100 m, you'd set the raster stat **max** to `98` (elevation ≤ 98 m) and leave the min very low (or set it to your DEM's lowest plausible value) to express "deep enough."

Click **Use Default Values** at any time to reset all six fields to the defaults above. These defaults are just a starting point — set them based on the actual distribution of your own data (the Tab 1 min/max/mean/stdev report is meant to help you pick sensible thresholds).

### Steps

1. **Input vector layer**: select the `_stat` layer from Tab 1 (must already contain `Area`, `Ratio`, and the raster statistic field).
2. Set your thresholds, or click **Use Default Values**.
3. Click **Run Filter & Create Layer**.
4. Every feature satisfying **all conditions simultaneously** — `Area` in range **AND** `Ratio` in range **AND** the raster statistic in range — is copied into a new layer named `<input_name>_filtered`, added to the map.
   - If Tab 1 hasn't been run yet (no raster statistic field is known), you'll be notified and filtering proceeds on Area and Ratio only.
5. If nothing matches, you'll get a "No matches" message and no layer is created.

## 6. Recommended workflow

1. Load your candidate anomaly polygons and the reference raster (e.g. a DEM).
2. **Compute Stats** (Tab 1) — pick the band and statistic that matches what you're checking for (e.g. Min elevation for depth), and run it on the raw candidates. Inspect the summary popup / saved report to understand the actual range of `Area`, `Ratio`, and the raster statistic in your dataset.
3. **Filter Features** (Tab 2), using the `_stat` layer as input and thresholds informed by step 2.
4. Visually spot-check the `_filtered` layer against imagery or the DEM before treating it as final.

## 7. Limitations & things to watch for

- **Shape assumption**: the geometric fields only make sense for simple 4‑vertex polygons (5 points including closure). Anything else (triangles, complex/multi-part polygons, polygons with extra vertices) gets `Length = Width = Area = Ratio = 0` and will typically fail every filter threshold.
- **Area is not the true polygon area** — it's `Length × Width` from two adjacent edges, which is only geometrically correct for true rectangles.
- **Vertex order matters**: Length/Width are derived from vertices 1‑2‑3 specifically, so the result depends on how your source data orders each polygon's ring.
- **Only one raster statistic per run** — if you need both, say, Min and Range from the same DEM, run Tab 1 twice with different statistic selections and note that the second run will delete the first run's raster field (any field starting with `raster_` is cleared before each new zonal-statistics pass).
- Tab 1 must be re-run any time the input vector, raster, band, or statistic selection changes; Tab 2 always filters on whatever raster field was produced by the most recent Tab 1 run.
