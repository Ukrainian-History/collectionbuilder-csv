# Map visualization

Powered by Leaflet.js, https://github.com/Leaflet/Leaflet

Following best practices listed in the Leaflet guide to accessibility, https://leafletjs.com/examples/accessibility/

With plugins: 

- search, https://github.com/naomap/leaflet-fusesearch
- cluster, https://github.com/Leaflet/Leaflet.markercluster
- cluster plugin (for search and cluster to work together), https://github.com/ghybs/Leaflet.MarkerCluster.Freezable
- full screen, https://github.com/Leaflet/Leaflet.fullscreen

## theme Configuration Options

Set base configuration in "_data/theme.yml" Map section, including:

```
auto-center-map: true # have the map auto fit all features into its view
latitude: 46.727485 # to manually center map if not using auto-center-map option
longitude: -117.014185 # to manually center map if not using auto-center-map option
zoom-level: 5 # zoom level for map if not using auto-center-map option
map-base: Esri_WorldStreetMap # set default base map, choose from: Esri_WorldStreetMap, Esri_NatGeoWorldMap, Esri_WorldImagery, OpenStreetMap_Mapnik
map-search: true # not suggested with large collections
map-search-fuzziness: 0.35 # fuzzy search range from 1 = anything to 0 = exact match only
map-cluster: true # suggested for large collection or with many items in same location
map-cluster-radius: 25 # size of clusters, from ~ 10 to 80
```

These "theme" options will load the correct CSS and JS for leaflet features, while setting some configuration variables in the javascript.

With the default `auto-center-map: true` option, Leaflet will automatically center and zoom the map based on all the items added to the map--you do not need to set the latitude, longitude, or zoom-level. 
If you would like to manually set the center and zoom level for the map, set `auto-center-map: false` and set values for the latitude, longitude, and zoom-level.

The `map-search: true` option adds a Fuse-based client-side text search of the configured metadata fields (using leaflet-fusesearch plugin). 
The index is relatively slow, so you may want to set this option to `false` if you find the map lagging.

The `map-cluster: true` option clusters features on the map (using Leaflet.markercluster plugin).
Because of the way markers are handled, for larger collections it is strongly suggested to keep cluster on, since it makes loading and navigating the map significantly more efficient.
The `map-cluster-radius` sets the maximum radius a cluster can cover in pixels on the map.
A smaller radius will create more, smaller clusters, and increasing will create fewer, larger clusters on the map.

## map-config.csv Options

The metadata displayed on object popups and included in search is configured using using "_data/map-config.csv":

- `field`: matches a column name in the metadata csv that will be displayed in object popups.
- `display`: display name for the field to appear on popup. if blank, field will not be displayed (but could be used in search)
- `search`: `true` or `false`/blank. If theme has `map-search` as `true`, then fields with true in this column will be indexed and displayed on the map search feature.

## URL parameters 

The map page supports parsing a query string to set the center and display an Item popup. 
If the url includes a query string, it will be parsed and set as the map view box with full zoom and open the popup.

Item pages that have lat/long will generate a "View on Map" button link. 
These link to the "map.html" page with a query string created from their lat long and objectid.

For example: 
`/map.html?location=46.726113,-117.015671&marker=example_004`

This can be created using the Liquid:
`{{ '/map.html?location=' | append: page.latitude  | append: ',' | append: page.longitude | append: '&marker=' | append: page.objectid | relative_url }}`

## Customizing the Base Map

You can customize the base maps by editing the template code in "_includes/js/map-js.html".

There is three parts to add a new one:

1. Set up a variable containing the map layer information. For some example free base maps that follow the same pattern as we are using, you can copy the variable from [Leaflet Providers Preview](https://leaflet-extras.github.io/leaflet-providers/preview/).
2. Add name to the base map switcher. The `baseMaps` variable sets up the names that appear in the switcher that appears in the upper right of the map. It follows the pattern of `"Display Name": Map_Layer_var_name`.
3. Set the default base map. This is usually set in "theme.yml" as the `map-base` option. If you add a new layer variable, use that name instead of the default ones. Alternatively, in map-js.html you can edit the variable name used to load the base map (which is next after setting up the `baseMaps` var).

Keep in mind that some of the base maps in the free [Leaflet Providers Preview](https://leaflet-extras.github.io/leaflet-providers/preview/) may have usage limitations--check the [Leaflet Providers readme for notes](https://github.com/leaflet-extras/leaflet-providers).
If you want to do more customization, check the [Leaflet docs](https://leafletjs.com/reference.html), and [Leaflet basemap providers plugins](https://leafletjs.com/plugins.html#basemap-providers).

## Custom CRS Tiles (advanced)

The map can display a locally generated tile set drawn in a **custom projection** instead of the standard Web Mercator base maps.
The main use case is a rotated CRS, where "north is not up"--for example, aligning the map to a site grid, a historic plan, or an excavation grid.
Item markers are still placed using the normal `latitude` / `longitude` metadata columns; they are reprojected into the custom CRS by [proj4js](https://github.com/proj4js/proj4js) and [Proj4Leaflet](https://github.com/kartena/Proj4Leaflet) at runtime, so no metadata changes are needed.

> **The web mercator base maps are not available in this mode.** Leaflet's CRS is fixed when the map is created, and the Esri / OpenStreetMap XYZ tiles only exist in Web Mercator. When `map-custom-crs` is true, those layers and the base map switcher are not added to the map, and `map-base` is ignored.

### theme.yml options

```
map-custom-crs: false # true / false - use the custom CRS tile set instead of the web mercator base maps
map-custom-crs-scheme: tiles-scheme # filename of the tile scheme json in _data/ (no .json extension)
map-custom-crs-tiles: /objects/tiles/{z}/{x}/{y}.png # tile url template, either a path in this project or a full external url
map-custom-crs-attribution: # attribution for the custom tiles, e.g. "Created using QGIS"
map-custom-crs-min-zoom: 2 # lowest zoom level to allow (tiles at very low zooms are tiny)
```

If `map-custom-crs-tiles` contains `://` it is used as-is (tiles hosted elsewhere), otherwise it is treated as a path within this project.
If `map-custom-crs` is true but the scheme file is missing or incomplete, the map falls back to the standard base maps and an HTML comment noting the problem is written into the page source.

### The tile scheme file

Copy the `tiles_scheme.json` written by the tile renderer into `_data/` (e.g. `_data/tiles-scheme.json`) and point `map-custom-crs-scheme` at it.
Jekyll reads JSON in `_data/`, so the values are written directly into the map javascript at build time--there is no extra request from the browser.

| Key | Meaning |
|---|---|
| `proj4` | proj4 string of the tile CRS |
| `origin` | CRS coordinates of the top left corner of tile `(0,0)` |
| `resolutions` | CRS units per pixel for each zoom level (array index = zoom) |
| `bounds` | `[xmin, ymin, xmax, ymax]` of the tiled extent, in CRS units |
| `zmax` | deepest zoom level |
| `tile_size` | tile size in pixels (256) |

The view is constrained to the tiled extent: the map sets `maxBounds` from the projected corners of `bounds`, and zoom is clamped between `map-custom-crs-min-zoom` and `zmax`.
With `auto-center-map: true`, the map still fits to the collection items, but falls back to fitting the whole tile set if the items fall outside the tiled area.

### Generating tiles

Use `utilities/render_tiles.py` in the QGIS Python console (`Plugins > Python Console`):

```python
exec(open('/path/to/utilities/render_tiles.py').read())
```

QGIS's built in "Generate XYZ tiles" tool always renders into EPSG:3857 and discards any custom CRS rotation, which is why this script exists.
It renders each tile directly in the project CRS, so scale dependent visibility works per zoom level and labels stay upright even when the grid is rotated.
Set `TEST_ONLY = True` first to write a single `test_render.png` to check orientation and content, then set it to `False` for the full run.

For a rotated grid, define a custom CRS in QGIS (**Settings > Custom Projections**) along these lines and set it as the project CRS:

```
+proj=omerc +lat_0=40.5462477 +lonc=-74.5215446 +alpha=0 +gamma=142 +k=1 +x_0=0 +y_0=0 +no_uoff +ellps=WGS84 +units=m +no_defs
```

`+gamma` is the rotation of grid north from true north.
**Use `+lonc`, never `+lon_0`**: PROJ ignores `+lon_0` for `omerc` and silently falls back to the Greenwich meridian, which collapses the map into a tiny blob.

### Hosting the tiles

A tile set is often tens of thousands of small files, which Jekyll will copy into `_site` on every rebuild.

- **External hosting (recommended for GitHub Pages):** upload the tiles to a CDN, object store, or separate repository and put the full url in `map-custom-crs-tiles`. Nothing else is needed.
- **In this project:** put the tiles in e.g. `objects/tiles/`, then add that directory to the `exclude` list in `_config.yml`, uncomment the `keep_files` line there, and run `bundle exec rake sync_tiles` once after building. Jekyll will then leave the tiles alone on later builds.

### Troubleshooting

- **Blank map or tiny content** -- the CRS string uses `+lon_0` instead of `+lonc`, or the rendered extent covered far more than the site.
- **Markers in the wrong place** -- check that `proj4` in the scheme file matches the CRS the tiles were rendered in.
- **Cannot pan to the whole map (view pinched in one direction)** -- an axis aligned lat lng `maxBounds` is invalid for a rotated CRS. Leaflet projects only `getNorthEast()` and `getSouthWest()` and axis aligns them, which lands inside the rotated tile rectangle. The map therefore overrides those two getters on `crsMaxBounds` so they project to the true tile corners; do not replace it with the plain lat lng AABB of the corners.
- **Browser crash or error on zoom** -- the map sets `crs._projectedBounds`, which is required whenever a Proj4Leaflet CRS has `bounds`; do not remove it.
- **proj4 returns NaN** -- `assets/lib/leaflet/proj4.js` must be version 2.14 or later, since earlier versions silently drop `+gamma`.
- **Map orientation is wrong or mirrored** -- verify the rotation in QGIS first; the browser just follows the CRS definition in the scheme file.