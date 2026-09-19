"""Render XYZ map tiles from a QGIS project in the project's own CRS.

Run inside the QGIS Python console:

    exec(open('/path/to/utilities/render_tiles.py').read())

QGIS's built in "Generate XYZ tiles" always renders into EPSG:3857 and discards any
custom CRS rotation. This script instead renders each tile directly in the project CRS,
so a rotated ("north is not up") custom CRS survives into the tiles, while labels stay
upright. It writes tiles to prod_tiles/{z}/{x}/{y}.png plus prod_tiles/tiles_scheme.json.

The scheme json is what CollectionBuilder's map reads: copy it into _data/ and set
map-custom-crs options in _data/theme.yml. See docs/maps.md for the full workflow.

Settings are the constants below (RES_MAX, MARGIN_M, METATILE, DPI, TEST_ONLY,
EXTENT_MODE, EXTENT, BACKGROUND).

This file is excluded from the Jekyll build in _config.yml.
"""

import json
import math
import os

from PyQt5.QtCore import QSize
from PyQt5.QtGui import QColor, QImage

from qgis.core import (
    QgsCoordinateTransform,
    QgsLabelingEngineSettings,
    QgsMapRendererSequentialJob,
    QgsMapSettings,
    QgsProject,
    QgsRectangle,
)

RES_MAX = 0.0284
MARGIN_M = 50.0
METATILE = 4
DPI = 96
TILE = 256
BACKGROUND = QColor(0, 0, 0, 0)
TEST_ONLY = False
EXTENT_MODE = "canvas"
EXTENT = None

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


def make_settings(rect, width, height):
    settings = QgsMapSettings()
    settings.setLayers(layers)
    settings.setExtent(rect)
    settings.setOutputSize(QSize(width, height))
    settings.setDestinationCrs(crs)
    settings.setOutputDpi(DPI)
    settings.setBackgroundColor(BACKGROUND)
    settings.setOutputImageFormat(QImage.Format_ARGB32_Premultiplied)
    settings.setFlag(QgsMapSettings.Antialiasing, True)
    settings.setFlag(QgsMapSettings.DrawLabeling, True)
    settings.setTransformContext(tc)
    settings.setExpressionContext(project.createExpressionContext())
    labeling = settings.labelingEngineSettings()
    labeling.setFlag(QgsLabelingEngineSettings.UsePartialCandidates, False)
    settings.setLabelingEngineSettings(labeling)
    return settings


def render_block(rect, width, height):
    settings = make_settings(rect, width, height)
    job = QgsMapRendererSequentialJob(settings)
    job.start()
    job.waitForFinished()
    for error in job.errors():
        print("render error: %s" % error.toString(), flush=True)
    return job.renderedImage()


def content_coverage(image):
    counted = 0
    samples = 0
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            samples += 1
            if image.pixelColor(x, y) != BACKGROUND:
                counted += 1
    return 100.0 * counted / samples if samples else 0.0


def render_test():
    res_test = extent.width() / 1024.0
    width = 1024
    height = max(1, int(round(extent.height() / res_test)))
    rect = QgsRectangle(extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum())
    print("rendering test image %dx%d at %.3f m/px -> %s" % (width, height, res_test, TEST_IMAGE_PATH), flush=True)
    image = render_block(rect, width, height)
    image.save(TEST_IMAGE_PATH, "PNG")
    print("test image content coverage: %.1f%%" % content_coverage(image), flush=True)


def generate_tiles():
    total_tiles = 0
    first_block_checked = False
    for z in range(zmax + 1):
        tile_span = TILE * resolutions[z]
        tx0 = int(math.floor((extent.xMinimum() - origin_x) / tile_span))
        tx1 = int(math.floor((extent.xMaximum() - origin_x) / tile_span))
        ty0 = int(math.floor((origin_y - extent.yMaximum()) / tile_span))
        ty1 = int(math.floor((origin_y - extent.yMinimum()) / tile_span))
        for mx in range(tx0, tx1 + 1, METATILE):
            for my in range(ty0, ty1 + 1, METATILE):
                mtx1 = min(mx + METATILE, tx1 + 1)
                mty1 = min(my + METATILE, ty1 + 1)
                block_w = (mtx1 - mx) * TILE
                block_h = (mty1 - my) * TILE
                rect = QgsRectangle(
                    origin_x + mx * tile_span,
                    origin_y - mty1 * tile_span,
                    origin_x + mtx1 * tile_span,
                    origin_y - my * tile_span,
                )
                block = render_block(rect, block_w, block_h)
                if not first_block_checked:
                    cov = content_coverage(block)
                    print("first metatile content coverage: %.1f%%" % cov, flush=True)
                    if cov < 1.0:
                        print("WARNING: first metatile appears blank - check test_render.png, extent, CRS, layer visibility", flush=True)
                    first_block_checked = True
                for tx in range(mx, mtx1):
                    for ty in range(my, mty1):
                        tile_dir = os.path.join(OUTPUT_DIR, str(z), str(tx))
                        os.makedirs(tile_dir, exist_ok=True)
                        tile_img = block.copy((tx - mx) * TILE, (ty - my) * TILE, TILE, TILE)
                        tile_img.save(os.path.join(tile_dir, "%d.png" % ty), "PNG")
                        total_tiles += 1
        print("zoom %d done (%d tiles total)" % (z, total_tiles), flush=True)

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
    print("wrote %s (%d tiles, zooms 0..%d)" % (SCHEME_PATH, total_tiles, zmax), flush=True)


if TEST_ONLY:
    render_test()
    print("TEST_ONLY mode: no tiles written. Set TEST_ONLY = False and re-run to generate tiles.", flush=True)
else:
    generate_tiles()