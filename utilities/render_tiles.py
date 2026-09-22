"""Render XYZ map tiles from a QGIS project in the project's own CRS.

Run inside the QGIS Python console:

    exec(open('/path/to/utilities/render_tiles.py').read())

QGIS's built in "Generate XYZ tiles" always renders into EPSG:3857 and discards any
custom CRS rotation. This script instead renders each tile directly in the project CRS,
so a rotated ("north is not up") custom CRS survives into the tiles, while labels stay
upright. It writes tiles to the project folder's prod_tiles plus prod_tiles/tiles_scheme.json.

The scheme json is what CollectionBuilder's map reads: copy it into _data/ and set
map-custom-crs options in _data/theme.yml. See docs/maps.md for the full workflow.

Settings are the constants below (RES_MAX, MARGIN_M, METATILE, DPI, TEST_ONLY,
EXTENT_MODE, EXTENT, BACKGROUND, ZOOMS, SKIP_EXISTING, PARALLEL, MAX_THREADS,
RUN_AS_TASK, PROFILE, PNG_QUALITY).

Depth: RES_MAX is the ground resolution at the deepest zoom. zmax and every coarser
resolution are derived from RES_MAX and the extent, so halving RES_MAX (e.g. 0.0284 to
0.0142) adds exactly one zoom level with twice the detail while leaving every existing
level's resolution and origin unchanged. With SKIP_EXISTING = True, adding that deeper
level later only renders the new tiles:

    RES_MAX = 0.0142    zooms 0..9
    RES_MAX = 0.0284    zooms 0..8
    RES_MAX = 0.0568    zooms 0..7

Rendering: each metatile is rendered as one image and sliced into tile PNGs. With
PARALLEL = True the layers are rendered in parallel with QgsMapRendererParallelJob;
labels are placed in a separate phase after the layer passes, so label placement and
collision handling are the same as a sequential render. RUN_AS_TASK = True runs the
whole thing as a QgsTask so QGIS stays responsive and the job can be canceled. A
metatile is skipped when all of its tile PNGs already exist, so a canceled or crashed
run can simply be started again.

This file is excluded from the Jekyll build in _config.yml.
"""

import gc
import json
import math
import os
import time

from PyQt5.QtCore import QCoreApplication, QSize, QThreadPool
from PyQt5.QtGui import QColor, QImage

from qgis.core import (
    QgsApplication,
    QgsCoordinateTransform,
    QgsLabelingEngineSettings,
    QgsMapRendererParallelJob,
    QgsMapRendererSequentialJob,
    QgsMapSettings,
    QgsProject,
    QgsRectangle,
    QgsTask,
    QgsVectorLayer,
)

RES_MAX = 0.0284
MARGIN_M = 50.0
METATILE = 8
DPI = 96
TILE = 256
BACKGROUND = QColor(0, 0, 0, 0)
TEST_ONLY = False
EXTENT_MODE = "canvas"
EXTENT = None

ZOOMS = None
SKIP_EXISTING = True
PARALLEL = True
MAX_THREADS = max(1, (os.cpu_count() or 2) - 1)
RUN_AS_TASK = True
PROFILE = False
PROFILE_ZOOM = None
GC_EVERY = 50
PNG_QUALITY = -1

ACTIVE_TASK = None

project = QgsProject.instance()
base_dir = os.path.dirname(project.fileName()) if project.fileName() else os.getcwd()
OUTPUT_DIR = os.path.join(base_dir, "prod_tiles")
SCHEME_PATH = os.path.join(OUTPUT_DIR, "tiles_scheme.json")
TEST_IMAGE_PATH = os.path.join(base_dir, "test_render.png")

crs = project.crs()
tc = project.transformContext()

proj4_def = crs.toProj4().strip()
normalized = proj4_def.replace("+lon_0=", "+lonc=").replace("+type=crs", "").strip()
if "+lon_0=" in proj4_def:
    print("NOTE: project CRS uses +lon_0 (invalid for omerc in proj4js); the scheme uses +lonc", flush=True)
if "+proj=omerc" not in normalized:
    print("WARNING: project CRS does not look like an omerc projection:", flush=True)
    print("  %s" % proj4_def, flush=True)
print("project CRS:      %s" % (crs.description() or crs.authid() or "custom"), flush=True)
print("project CRS proj4: %s" % proj4_def, flush=True)
print("scheme proj4:      %s" % normalized, flush=True)

layers = iface.mapCanvas().layers()
if not layers:
    raise RuntimeError("No visible layers on the map canvas.")
print("rendering %d visible layer(s)" % len(layers), flush=True)
HAS_LABELS = any(
    isinstance(layer, QgsVectorLayer) and layer.labeling() is not None
    for layer in layers
)

if EXTENT_MODE == "canvas":
    canvas = iface.mapCanvas()
    extent = canvas.extent()
    canvas_crs = canvas.mapSettings().destinationCrs()
    if canvas_crs.isValid() and canvas_crs != crs:
        extent = QgsCoordinateTransform(
            canvas_crs, crs, tc
        ).transformBoundingBox(extent)
    print("extent source: map canvas", flush=True)
elif EXTENT_MODE == "explicit":
    if not EXTENT or len(EXTENT) != 4:
        raise RuntimeError("EXTENT_MODE is 'explicit' but EXTENT is not a [xmin, ymin, xmax, ymax] list.")
    extent = QgsRectangle(EXTENT[0], EXTENT[1], EXTENT[2], EXTENT[3])
    print("extent source: explicit EXTENT %s" % EXTENT, flush=True)
elif EXTENT_MODE == "layers":
    extent = QgsRectangle()
    for layer in layers:
        layer_extent = layer.extent()
        if layer.crs().isValid() and layer.crs() != crs:
            layer_extent = QgsCoordinateTransform(
                layer.crs(), crs, tc
            ).transformBoundingBox(layer_extent)
        extent.combineExtentWith(layer_extent)
    print("extent source: union of visible layer extents", flush=True)
else:
    raise RuntimeError("Unknown EXTENT_MODE: %s" % EXTENT_MODE)

if extent.isEmpty():
    raise RuntimeError("Could not determine a valid extent.")

extent = extent.buffered(MARGIN_M)
print("tiling extent (CRS m): %s" % extent.toString(), flush=True)
print("extent size (m): %.1f x %.1f" % (extent.width(), extent.height()), flush=True)

max_dim = max(extent.width(), extent.height())
zmax = int(math.ceil(math.log(max_dim / (TILE * RES_MAX), 2)))
resolutions = [RES_MAX * (2 ** (zmax - z)) for z in range(zmax + 1)]
origin_x = extent.xMinimum()
origin_y = extent.yMaximum()
print("origin: [%.3f, %.3f]  zooms: 0..%d" % (origin_x, origin_y, zmax), flush=True)
print("resolutions: %s" % ", ".join("%.4f" % r for r in resolutions), flush=True)

if ZOOMS is None:
    zooms_to_render = list(range(zmax + 1))
    zoom_label = "0..%d" % zmax
else:
    zooms_to_render = sorted(set(ZOOMS))
    invalid = [z for z in zooms_to_render if z < 0 or z > zmax]
    if invalid:
        raise RuntimeError("ZOOMS contains levels outside 0..%d: %s" % (zmax, invalid))
    zoom_label = ", ".join(str(z) for z in zooms_to_render)

if PARALLEL and MAX_THREADS:
    QThreadPool.globalInstance().setMaxThreadCount(MAX_THREADS)

print("zoom levels:       %s" % zoom_label, flush=True)
print("labels:            %s" % ("yes" if HAS_LABELS else "no"), flush=True)
print("parallel render:   %s (max %s threads)" % (PARALLEL, MAX_THREADS if PARALLEL else "n/a"), flush=True)
print("skip existing:     %s" % SKIP_EXISTING, flush=True)
print("metatile:          %d x %d tiles (%d px blocks)" % (METATILE, METATILE, METATILE * TILE), flush=True)


def make_settings():
    settings = QgsMapSettings()
    settings.setLayers(layers)
    settings.setDestinationCrs(crs)
    settings.setOutputDpi(DPI)
    settings.setBackgroundColor(BACKGROUND)
    settings.setOutputImageFormat(QImage.Format_ARGB32_Premultiplied)
    settings.setFlag(QgsMapSettings.Antialiasing, True)
    settings.setFlag(QgsMapSettings.DrawLabeling, HAS_LABELS)
    settings.setTransformContext(tc)
    settings.setExpressionContext(project.createExpressionContext())
    labeling = settings.labelingEngineSettings()
    labeling.setFlag(QgsLabelingEngineSettings.UsePartialCandidates, False)
    settings.setLabelingEngineSettings(labeling)
    return settings


base_settings = make_settings()


def render_errors(job):
    for error in job.errors():
        print("render error [%s]: %s" % (error.layerID, error.message), flush=True)


def render_block(rect, width, height):
    base_settings.setExtent(rect)
    base_settings.setOutputSize(QSize(width, height))
    if PARALLEL:
        job = QgsMapRendererParallelJob(base_settings)
    else:
        job = QgsMapRendererSequentialJob(base_settings)
    started = time.perf_counter()
    job.start()
    job.waitForFinished()
    elapsed = time.perf_counter() - started
    image = job.renderedImage()
    render_errors(job)
    del job
    return image, elapsed


def render_block_sequential_profiled(rect, width, height):
    base_settings.setExtent(rect)
    base_settings.setOutputSize(QSize(width, height))
    job = QgsMapRendererSequentialJob(base_settings)
    layer_times = {}
    marks = [time.perf_counter()]

    def on_layer_rendered(layer_id):
        now = time.perf_counter()
        layer_times[layer_id] = layer_times.get(layer_id, 0.0) + (now - marks[0])
        marks[0] = now

    if hasattr(job, "layerRendered"):
        job.layerRendered.connect(on_layer_rendered)
    else:
        print("layerRendered signal not available; per-layer timings skipped", flush=True)
    started = time.perf_counter()
    job.start()
    while job.isActive():
        QCoreApplication.processEvents()
    job.waitForFinished()
    elapsed = time.perf_counter() - started
    image = job.renderedImage()
    render_errors(job)
    del job
    names = {layer.id(): layer.name() for layer in layers}
    print("sequential metatile: %.2fs total" % elapsed, flush=True)
    for layer_id, seconds in sorted(layer_times.items(), key=lambda item: -item[1]):
        print("  %.2fs  %s" % (seconds, names.get(layer_id, layer_id)), flush=True)
    return image, elapsed


def image_difference(first, second):
    if first.width() != second.width() or first.height() != second.height():
        return 100.0
    counted = 0
    samples = 0
    for y in range(0, first.height(), 2):
        for x in range(0, first.width(), 2):
            samples += 1
            if first.pixel(x, y) != second.pixel(x, y):
                counted += 1
    return 100.0 * counted / samples if samples else 0.0


def content_coverage(image):
    counted = 0
    samples = 0
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            samples += 1
            if image.pixelColor(x, y) != BACKGROUND:
                counted += 1
    return 100.0 * counted / samples if samples else 0.0


def tile_path(z, x, y):
    return os.path.join(OUTPUT_DIR, str(z), str(x), "%d.png" % y)


def block_is_complete(z, mx, my, mtx1, mty1):
    for x in range(mx, mtx1):
        for y in range(my, mty1):
            if not os.path.exists(tile_path(z, x, y)):
                return False
    return True


def save_block_tiles(z, mx, my, mtx1, mty1, block):
    for x in range(mx, mtx1):
        tile_dir = os.path.join(OUTPUT_DIR, str(z), str(x))
        os.makedirs(tile_dir, exist_ok=True)
        for y in range(my, mty1):
            tile_img = block.copy((x - mx) * TILE, (y - my) * TILE, TILE, TILE)
            target = os.path.join(tile_dir, "%d.png" % y)
            if PNG_QUALITY >= 0:
                tile_img.save(target, "PNG", PNG_QUALITY)
            else:
                tile_img.save(target, "PNG")


def metatile_rect(z, mx, my, mtx1, mty1):
    tile_span = TILE * resolutions[z]
    return QgsRectangle(
        origin_x + mx * tile_span,
        origin_y - mty1 * tile_span,
        origin_x + mtx1 * tile_span,
        origin_y - my * tile_span,
    )


def metatile_bounds(z):
    tile_span = TILE * resolutions[z]
    tx0 = int(math.floor((extent.xMinimum() - origin_x) / tile_span))
    tx1 = int(math.floor((extent.xMaximum() - origin_x) / tile_span))
    ty0 = int(math.floor((origin_y - extent.yMaximum()) / tile_span))
    ty1 = int(math.floor((origin_y - extent.yMinimum()) / tile_span))
    return tx0, tx1, ty0, ty1


def format_duration(seconds):
    seconds = int(round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return "%dh%02dm" % (hours, minutes)
    if minutes:
        return "%dm%02ds" % (minutes, secs)
    return "%ds" % secs


def is_canceled():
    return ACTIVE_TASK is not None and ACTIVE_TASK.isCanceled()


def render_test():
    res_test = extent.width() / 1024.0
    width = 1024
    height = max(1, int(round(extent.height() / res_test)))
    rect = QgsRectangle(extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum())
    print("rendering test image %dx%d at %.3f m/px -> %s" % (width, height, res_test, TEST_IMAGE_PATH), flush=True)
    image, elapsed = render_block(rect, width, height)
    image.save(TEST_IMAGE_PATH, "PNG")
    print("test image rendered in %.1fs, content coverage: %.1f%%" % (elapsed, content_coverage(image)), flush=True)


def profile():
    z = zmax if PROFILE_ZOOM is None else PROFILE_ZOOM
    if z < 0 or z > zmax:
        raise RuntimeError("PROFILE_ZOOM must be between 0 and %d." % zmax)
    tx0, tx1, ty0, ty1 = metatile_bounds(z)
    mx = ((tx0 + tx1) // (2 * METATILE)) * METATILE
    my = ((ty0 + ty1) // (2 * METATILE)) * METATILE
    mtx1 = min(mx + METATILE, tx1 + 1)
    mty1 = min(my + METATILE, ty1 + 1)
    block_w = (mtx1 - mx) * TILE
    block_h = (mty1 - my) * TILE
    rect = metatile_rect(z, mx, my, mtx1, mty1)
    print("profiling metatile z%d at (%d, %d), %dx%d px" % (z, mx, my, block_w, block_h), flush=True)
    first_image, sequential_seconds = render_block_sequential_profiled(rect, block_w, block_h)
    first_path = os.path.join(base_dir, "profile_sequential.png")
    first_image.save(first_path, "PNG")
    print("wrote %s" % first_path, flush=True)
    if not PARALLEL:
        print("PARALLEL is False; skipping the parallel comparison.", flush=True)
        return
    second_image, parallel_seconds = render_block(rect, block_w, block_h)
    second_path = os.path.join(base_dir, "profile_parallel.png")
    second_image.save(second_path, "PNG")
    difference = image_difference(first_image, second_image)
    speedup = sequential_seconds / parallel_seconds if parallel_seconds else 0.0
    print("parallel metatile: %.2fs total (sequential %.2fs, %.2fx faster)" % (parallel_seconds, sequential_seconds, speedup), flush=True)
    print("pixel difference between sequential and parallel: %.3f%%" % difference, flush=True)
    print("wrote %s" % second_path, flush=True)
    print("check the two PNGs; if labels or features differ, set PARALLEL = False.", flush=True)


def generate_tiles():
    covered_tiles = 0
    written_tiles = 0
    rendered_blocks = 0
    skipped_blocks = 0
    first_block_checked = False
    run_started = time.perf_counter()
    for z in zooms_to_render:
        tx0, tx1, ty0, ty1 = metatile_bounds(z)
        starts_x = list(range(tx0, tx1 + 1, METATILE))
        starts_y = list(range(ty0, ty1 + 1, METATILE))
        zoom_blocks = len(starts_x) * len(starts_y)
        done_blocks = 0
        zoom_render_seconds = 0.0
        zoom_rendered = 0
        print("zoom %d: %d metatile(s) to consider" % (z, zoom_blocks), flush=True)
        for mx in starts_x:
            for my in starts_y:
                if is_canceled():
                    print("cancel requested; stopped at zoom %d metatile (%d, %d)" % (z, mx, my), flush=True)
                    return False
                mtx1 = min(mx + METATILE, tx1 + 1)
                mty1 = min(my + METATILE, ty1 + 1)
                block_tiles = (mtx1 - mx) * (mty1 - my)
                covered_tiles += block_tiles
                if SKIP_EXISTING and block_is_complete(z, mx, my, mtx1, mty1):
                    skipped_blocks += 1
                    done_blocks += 1
                    continue
                block_w = (mtx1 - mx) * TILE
                block_h = (mty1 - my) * TILE
                rect = metatile_rect(z, mx, my, mtx1, mty1)
                block, elapsed = render_block(rect, block_w, block_h)
                if not first_block_checked:
                    coverage = content_coverage(block)
                    print("first metatile content coverage: %.1f%%" % coverage, flush=True)
                    if coverage < 1.0:
                        print("WARNING: first metatile appears blank - check test_render.png, extent, CRS, layer visibility", flush=True)
                    first_block_checked = True
                save_block_tiles(z, mx, my, mtx1, mty1, block)
                del block
                written_tiles += block_tiles
                rendered_blocks += 1
                zoom_rendered += 1
                zoom_render_seconds += elapsed
                done_blocks += 1
                if rendered_blocks % GC_EVERY == 0:
                    gc.collect()
                average = zoom_render_seconds / zoom_rendered
                remaining = zoom_blocks - done_blocks
                print(
                    "z%d %d/%d metatiles (%.0f%%) last %.1fs avg %.1fs ETA %s, %d new tiles (%d/%d covered)"
                    % (z, done_blocks, zoom_blocks, 100.0 * done_blocks / zoom_blocks, elapsed, average,
                       format_duration(average * remaining), written_tiles, written_tiles, covered_tiles),
                    flush=True,
                )
        print("zoom %d done (%d new tiles, %d metatiles rendered, %d skipped this run)"
              % (z, written_tiles, rendered_blocks, skipped_blocks), flush=True)
        gc.collect()

    scheme = {
        "proj4": normalized,
        "origin": [origin_x, origin_y],
        "resolutions": resolutions,
        "bounds": [extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()],
        "zmax": zmax,
        "tile_size": TILE,
    }
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(SCHEME_PATH, "w") as f:
        json.dump(scheme, f, indent=2)
    print("wrote %s (%d new tiles, %d tiles covered, zooms %s)" % (SCHEME_PATH, written_tiles, covered_tiles, zoom_label), flush=True)
    print("run time: %s (%d metatiles rendered, %d skipped)" % (format_duration(time.perf_counter() - run_started), rendered_blocks, skipped_blocks), flush=True)
    return True


class TileRenderTask(QgsTask):
    def __init__(self):
        super().__init__("Render XYZ tiles", QgsTask.CanCancel)

    def run(self):
        global ACTIVE_TASK
        ACTIVE_TASK = self
        try:
            return generate_tiles()
        finally:
            ACTIVE_TASK = None

    def finished(self, result):
        if result:
            print("tile rendering finished", flush=True)
        else:
            print("tile rendering stopped (canceled or failed); run again to resume", flush=True)


if TEST_ONLY:
    render_test()
    print("TEST_ONLY mode: no tiles written. Set TEST_ONLY = False and re-run to generate tiles.", flush=True)
elif PROFILE:
    profile()
    print("PROFILE mode: no tiles written. Set PROFILE = False and re-run to generate tiles.", flush=True)
elif RUN_AS_TASK:
    task = TileRenderTask()
    QgsApplication.taskManager().addTask(task)
    print("tile rendering submitted as a background task; QGIS stays responsive (cancel it in the task manager)", flush=True)
else:
    generate_tiles()
