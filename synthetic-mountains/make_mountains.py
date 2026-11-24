#!/usr/bin/env python3
"""SYNTHETIC mountain region for Karmania: terrain, folded geology, river, as files Karmania can import.

Everything here is invented by formulas (no real place, no real survey). Pure standard library.

Usage: python3 make_mountains.py <out_dir> [--preview <dir>]

Region: 30 km x 30 km, local x,y in metres from the south-west corner, placed at E0,N0 in UTM zone 32N.
Units (codes): 1 Alluvium & Sediments, 2 Sedimentary Rocks, 3 Volcanic Rocks,
               4 Igneous Intrusions, 5 Metamorphic Rocks, 6 Basement Rock
"""
import csv
import math
import os
import struct
import sys
import zlib

W = 30000.0                      # side of the region, m
E0, N0 = 480000.0, 5150000.0     # where the south-west corner is, UTM 32N (arbitrary; no real place)
BLOCK = (300.0, 300.0, 100.0)    # block size, m
Z_BOTTOM = -1800.0               # bottom of the block model
SEED = 7

UNITS = {1: "Alluvium & Sediments", 2: "Sedimentary Rocks", 3: "Volcanic Rocks",
         4: "Igneous Intrusions", 5: "Metamorphic Rocks", 6: "Basement Rock"}


# ------------------------------------------------------------------ noise
def _hash(ix, iy, seed):
    n = (ix * 374761393 + iy * 668265263 + seed * 2147483647) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    n ^= n >> 16
    return (n & 0xFFFFFF) / 0xFFFFFF


def _vnoise(x, y, seed):
    ix, iy = math.floor(x), math.floor(y)
    fx, fy = x - ix, y - iy
    u, v = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    a, b = _hash(ix, iy, seed), _hash(ix + 1, iy, seed)
    c, d = _hash(ix, iy + 1, seed), _hash(ix + 1, iy + 1, seed)
    return (a * (1 - u) + b * u) * (1 - v) + (c * (1 - u) + d * u) * v


def ridged(x, y, seed, octaves=5):
    s = tot = 0.0
    amp, f = 0.5, 1.0
    for o in range(octaves):
        n = 1 - abs(2 * _vnoise(x * f, y * f, seed + o * 31) - 1)
        s += amp * n * n
        tot += amp
        amp *= 0.5
        f *= 2
    return s / tot


# ------------------------------------------------------------------ terrain
PEAKS = [  # x, y, height above the base level (m), spread (m)
    (16000, 22500, 1700, 3300),   # main mountain
    (24500, 20000, 1300, 2900),   # east mountain (the intrusion is on its flank)
    (5500, 15500, 1300, 2700),    # west mountain (basement core)
    (20500, 23500, 650, 2300),    # ridge links
    (10500, 20000, 600, 2300),
    (13500, 8500, 520, 1700),     # hills
    (24500, 6500, 380, 2100),
    (4500, 4500, 300, 1900),
    (9500, 26500, 450, 2200),
]


def base_level(x, y):
    return 110 + 380 * (max(y, 0.0) / W) ** 1.4 + 110 * (1 - x / W)


def peaks_part(x, y):
    s = 0.0
    for px, py, a, sg in PEAKS:
        s += a * math.exp(-((x - px) ** 2 + (y - py) ** 2) / (2 * sg * sg))
    return s


# river centrelines (x, y control points), main river first; tributaries end on the main river
RIVER_CTRL = [
    [(14500, 19500), (13200, 16500), (14800, 13500), (17800, 11500), (19500, 8500), (18000, 5500), (20500, 3000), (24500, 1000), (27500, 0)],
    [(9000, 15000), (10500, 13500), (12800, 13800), (14800, 13500)],
    [(23500, 12500), (21500, 11000), (19800, 10600), (18000, 11700) ],
]


def _catmull(ctrl, step=100.0):
    pts = []
    p = [ctrl[0]] + ctrl + [ctrl[-1]]
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        n = max(2, int(math.hypot(p2[0] - p1[0], p2[1] - p1[1]) / step))
        for k in range(n):
            t = k / n
            t2, t3 = t * t, t * t * t
            pts.append(tuple(0.5 * ((2 * p1[a]) + (-p0[a] + p2[a]) * t + (2 * p0[a] - 5 * p1[a] + 4 * p2[a] - p3[a]) * t2
                                    + (-p0[a] + 3 * p1[a] - 3 * p2[a] + p3[a]) * t3) for a in (0, 1)))
    pts.append(ctrl[-1])
    return pts


def _meander(pts, amp, wave):
    out, run = [], 0.0
    for i, (x, y) in enumerate(pts):
        if i:
            run += math.hypot(x - pts[i - 1][0], y - pts[i - 1][1])
        j, k = max(i - 1, 0), min(i + 1, len(pts) - 1)
        dx, dy = pts[k][0] - pts[j][0], pts[k][1] - pts[j][1]
        n = math.hypot(dx, dy) or 1.0
        fade = min(1.0, i / 6, (len(pts) - 1 - i) / 6)       # no offset at the ends, so tributaries still join
        off = amp * fade * math.sin(2 * math.pi * run / wave)
        out.append((x - dy / n * off, y + dx / n * off))
    return out


RIVERS = [_meander(_catmull(RIVER_CTRL[0]), 380, 3200)] + [_meander(_catmull(c), 200, 2000) for c in RIVER_CTRL[1:]]
_BUCKET = 1500.0
_buckets = {}
for _r, _pts in enumerate(RIVERS):
    for _i, (_x, _y) in enumerate(_pts):
        _buckets.setdefault((int(_x // _BUCKET), int(_y // _BUCKET)), []).append((_x, _y, _r, _i))


def river_near(x, y):
    """(distance, river index, sample index) of the nearest river sample within about 1.5 km, else None."""
    bx, by = int(x // _BUCKET), int(y // _BUCKET)
    best = None
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for sx, sy, r, i in _buckets.get((bx + dx, by + dy), ()):
                d = math.hypot(x - sx, y - sy)
                if best is None or d < best[0]:
                    best = (d, r, i)
    return best


def _dem_raw(x, y):
    hp = peaks_part(x, y)
    h = base_level(x, y) + hp + (ridged(x / 1800, y / 1800, SEED) - 0.35) * (50 + 0.18 * hp)
    near = river_near(x, y)
    if near:
        d, r, _ = near
        h -= (190 if r == 0 else 90) * math.exp(-(d / (750 if r == 0 else 450)) ** 2)
    return max(h, 30.0)


# river bed elevation: never rises downstream; tributaries end level with the main river
_bed = []
for _r, _pts in enumerate(RIVERS):
    run, z = 1e9, []
    for (_x, _y) in _pts:
        run = min(run, _dem_raw(_x, _y) - 8)
        z.append(run)
    _bed.append(z)
for _r in range(1, len(RIVERS)):
    _x, _y = RIVERS[_r][-1]
    _i = min(range(len(RIVERS[0])), key=lambda i: math.hypot(_x - RIVERS[0][i][0], _y - RIVERS[0][i][1]))
    _zend = _bed[0][_i]
    n = len(_bed[_r])
    _bed[_r] = [max(z, _zend + 0.5 * (n - 1 - k)) for k, z in enumerate(_bed[_r])]


def dem(x, y):
    h = _dem_raw(x, y)
    near = river_near(x, y)
    if near and near[0] < 420:
        d, r, i = near
        h = min(h, _bed[r][i] + 22 * (d / 420) ** 2)
    return round(max(h, 30.0), 2)


# ------------------------------------------------------------------ geology
GAMMA = [1.95, 1.5, 2.15, 1.45, 1.45, 1.15, 1.15, 1.15, 1.15]   # how much faster the basement rises than the ground, per PEAKS entry
S0 = 1600.0                                                  # thickness of the stack above the basement on flat ground
STACK = [(200, 4), (500, 3), (800, 2), (1000, 3), (1250, 2), (1900, 5)]   # (top above basement, unit), bottom first
COVER = 5                                                    # anything higher is metamorphic
INTRUSIONS = [(23000, 17500, 250, 4800, 4300, 1900), (8500, 9000, -200, 2200, 2200, 1500)]   # centre x,y,z, radii


def basement_top(x, y):
    b = base_level(x, y) - S0
    for (px, py, a, sg), g in zip(PEAKS, GAMMA):
        b += g * a * math.exp(-((x - px) ** 2 + (y - py) ** 2) / (2 * (1.15 * sg) ** 2))
    return (b + 420 * math.sin(2 * math.pi * x / 15000 + 0.6) * math.cos(2 * math.pi * y / 19000 - 0.3)
            + 230 * math.sin(2 * math.pi * (x + y) / 26000 + 1.9))


def column(x, y):
    """Everything about one map position that the unit at any depth depends on."""
    h = dem(x, y)
    near = river_near(x, y)
    dr = near[0] if near else 1e9
    plain = max(0.0, min(1.0, (380 - h) / 200))
    thick = 220 * math.exp(-(dr / 550) ** 2) + 110 * plain * math.exp(-(dr / 1500) ** 2)
    shift = 250 * (2 * _vnoise(x / 2600, y / 2600, SEED + 99) - 1)        # the layers are not the same thickness everywhere
    wob = 1 + 0.28 * (2 * _vnoise(x / 1800, y / 1800, SEED + 5) - 1)      # the intrusions have ragged edges
    return (x, y, h, basement_top(x, y), thick, shift, wob)


def unit_at(col, z):
    x, y, h, b, thick, shift, wob = col
    if thick >= 15.0 and h - z <= thick:      # a layer thinner than 15 m is not mapped
        return 1
    for cx, cy, cz, rx, ry, rz in INTRUSIONS:
        if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 + ((z - cz) / rz) ** 2 <= wob * wob:
            return 4
    s = z - b + shift
    if s < 0:
        return 6
    for top, u in STACK:
        if s < top:
            return u
    return COVER


# ------------------------------------------------------------------ output
def utm_wkt():
    return ('PROJCS["WGS 84 / UTM zone 32N",GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],'
            'AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
            'AUTHORITY["EPSG","4326"]],PROJECTION["Transverse_Mercator"],PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",9],'
            'PARAMETER["scale_factor",0.9996],PARAMETER["false_easting",500000],PARAMETER["false_northing",0],UNIT["metre",1,AUTHORITY["EPSG","9001"]],'
            'AXIS["Easting",EAST],AXIS["Northing",NORTH],AUTHORITY["EPSG","32632"]]')


def png(path, w, h, rgb):
    raw = b"".join(b"\x00" + bytes(rgb[y * w * 3:(y + 1) * w * 3]) for y in range(h))
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    open(path, "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


PALETTE = {1: (150, 200, 225), 2: (235, 190, 90), 3: (160, 110, 170), 4: (225, 120, 85), 5: (160, 175, 75), 6: (125, 120, 115)}



def _fill_quad(img, W_, H_, pts, rgb):
    ys = [p[1] for p in pts]
    for y in range(max(0, int(math.floor(min(ys)))), min(H_ - 1, int(math.ceil(max(ys)))) + 1):
        xs = []
        n = len(pts)
        for a in range(n):
            (x0, y0), (x1, y1) = pts[a], pts[(a + 1) % n]
            if (y0 <= y < y1) or (y1 <= y < y0):
                xs.append(x0 + (y - y0) * (x1 - x0) / (y1 - y0))
        if len(xs) >= 2:
            lo, hi = max(0, int(math.floor(min(xs)))), min(W_ - 1, int(math.ceil(max(xs))))
            if hi >= lo:
                img[(y * W_ + lo) * 3:(y * W_ + hi + 1) * 3] = bytes(rgb) * (hi - lo + 1)


def render_iso(cols, nb, zc0, path, exag=4.0, width=1500, height=1000):
    """Block diagram seen from the south-east: tops, and the faces that show, painted far to near."""
    img = bytearray(b"\xff" * (width * height * 3))
    r2 = math.sqrt(0.5)
    phi = math.radians(33)
    scale = width * 0.92 / (W * 2 * r2)

    def proj(x, y, z):
        sx = (x + y) * r2
        d = (-x + y) * r2
        return (width * 0.04 + sx * scale, height * 0.72 - (d * math.sin(phi) + z * exag * math.cos(phi)) * scale + height * 0.0)

    def shade(c, f):
        return tuple(min(255, int(v * f)) for v in c)

    def kmax(i, j):
        if not (0 <= i < nb and 0 <= j < nb):
            return -1
        return math.floor((cols[(i, j)][2] - zc0) / BLOCK[2])

    dx, dy, dz = BLOCK
    for j in range(nb - 1, -1, -1):
        for i in range(nb):
            col = cols[(i, j)]
            kt = kmax(i, j)
            x0, x1, y0, y1 = i * dx, (i + 1) * dx, j * dy, (j + 1) * dy
            # south face (y = y0) and east face (x = x1), exposed blocks only
            for face, (ni, nj), f in (("S", (i, j - 1), 0.80), ("E", (i + 1, j), 0.62)):
                kn = kmax(ni, nj)
                for k in range(max(kn + 1, 0), kt + 1):
                    za, zb = Z_BOTTOM + k * dz, Z_BOTTOM + (k + 1) * dz
                    u = unit_at(col, zc0 + k * dz)
                    if face == "S":
                        q = [proj(x0, y0, za), proj(x1, y0, za), proj(x1, y0, zb), proj(x0, y0, zb)]
                    else:
                        q = [proj(x1, y0, za), proj(x1, y1, za), proj(x1, y1, zb), proj(x1, y0, zb)]
                    _fill_quad(img, width, height, q, shade(PALETTE[u], f))
            ztop = Z_BOTTOM + (kt + 1) * dz
            ut = unit_at(col, zc0 + kt * dz)
            _fill_quad(img, width, height, [proj(x0, y0, ztop), proj(x1, y0, ztop), proj(x1, y1, ztop), proj(x0, y1, ztop)], PALETTE[ut])
    png(path, width, height, img)


def main(out, preview=None):
    os.makedirs(out, exist_ok=True)
    nb = int(W / BLOCK[0])
    ncols = int(W / 100)

    # block columns
    cols = {}
    for j in range(nb):
        for i in range(nb):
            cols[(i, j)] = column((i + 0.5) * BLOCK[0], (j + 0.5) * BLOCK[1])
    zc0 = Z_BOTTOM + BLOCK[2] / 2
    nblocks = 0
    counts = {u: 0 for u in UNITS}
    with open(f"{out}/geology_block_model.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["XC", "YC", "ZC", "unit", "unit_code", "depth_m"])
        for j in range(nb):
            for i in range(nb):
                col = cols[(i, j)]
                h = col[2]
                kmax = math.floor((h - zc0) / BLOCK[2])
                for k in range(kmax + 1):
                    z = zc0 + k * BLOCK[2]
                    u = unit_at(col, z)
                    counts[u] += 1
                    nblocks += 1
                    w.writerow([f"{E0 + col[0]:.0f}", f"{N0 + col[1]:.0f}", f"{z:.0f}", UNITS[u], u, f"{h - z:.0f}"])
    open(f"{out}/geology_block_model.prj", "w").write(utm_wkt())
    print("blocks", nblocks, {UNITS[u]: c for u, c in counts.items()})

    # grids at 100 m cells: cell centres at 50 + 100 n
    dem_rows, geo_rows, hs_src = [], [], {}
    surf_counts = {u: 0 for u in UNITS}
    for r in range(ncols):                       # r = 0 is the south row; written north first below
        y = (r + 0.5) * 100
        drow, grow = [], []
        for c in range(ncols):
            x = (c + 0.5) * 100
            col = column(x, y)
            u = unit_at(col, col[2] - 1.0)
            drow.append(col[2])
            grow.append(u)
            surf_counts[u] += 1
        dem_rows.append(drow)
        geo_rows.append(grow)
    for name, rows, fmt in (("terrain_dem", dem_rows, "{:.2f}"), ("surface_geology", geo_rows, "{:d}")):
        with open(f"{out}/{name}.asc", "w") as f:
            f.write(f"ncols {ncols}\nnrows {ncols}\nxllcorner {E0:.0f}\nyllcorner {N0:.0f}\ncellsize 100\nNODATA_value -9999\n")
            for row in reversed(rows):
                f.write(" ".join(fmt.format(v) for v in row) + "\n")
        open(f"{out}/{name}.prj", "w").write(utm_wkt())
    print("surface cells", {UNITS[u]: c for u, c in surf_counts.items()})

    # smooth terrain mesh, 150 m spacing
    n = int(W / 150) + 1
    verts = []
    for j in range(n):
        for i in range(n):
            verts.append(dem(i * 150.0, j * 150.0))
    with open(f"{out}/terrain_surface.obj", "w") as f:
        f.write("# SYNTHETIC terrain, one vertex per 150 m; x, y in UTM 32N metres, z = elevation in metres.\n")
        for j in range(n):
            for i in range(n):
                f.write(f"v {E0 + i * 150.0:.1f} {N0 + j * 150.0:.1f} {verts[j * n + i]:.2f}\n")
        for j in range(n - 1):
            for i in range(n - 1):
                a, b, c, d = j * n + i + 1, j * n + i + 2, (j + 1) * n + i + 2, (j + 1) * n + i + 1
                f.write(f"f {a} {b} {c}\nf {a} {c} {d}\n")
    open(f"{out}/terrain_surface.prj", "w").write(utm_wkt())

    # rivers as 3D lines, on top of the block surface
    with open(f"{out}/rivers.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["X", "Y", "Z", "Line", "river"])
        for r, pts in enumerate(RIVERS):
            for (x, y) in pts:
                if not (0 <= x < W and 0 <= y < W):
                    continue
                col = cols[(int(x // BLOCK[0]), int(y // BLOCK[1]))]
                kmax = math.floor((col[2] - zc0) / BLOCK[2])
                top = zc0 + kmax * BLOCK[2] + BLOCK[2] / 2
                w.writerow([f"{E0 + x:.1f}", f"{N0 + y:.1f}", f"{top + 10:.1f}", r + 1, "main" if r == 0 else f"tributary {r}"])
    open(f"{out}/rivers.prj", "w").write(utm_wkt())

    if preview:
        os.makedirs(preview, exist_ok=True)
        # surface geology with hillshade, 2 px per cell
        S = 2
        rgb = bytearray()
        for r in range(ncols - 1, -1, -1):
            line = bytearray()
            for c in range(ncols):
                rr, cc = min(r + 1, ncols - 1), min(c + 1, ncols - 1)
                dz_x = dem_rows[r][cc] - dem_rows[r][max(c - 1, 0)]
                dz_y = dem_rows[rr][c] - dem_rows[max(r - 1, 0)][c]
                shade = max(0.25, min(1.3, 0.8 + (-dz_x * 0.55 + dz_y * 0.55) / 90))
                col = PALETTE[geo_rows[r][c]]
                px = bytes(min(255, int(v * shade)) for v in col)
                line += px * S
            rgb += bytes(line) * S
        png(f"{preview}/surface_geology.png", ncols * S, ncols * S, rgb)
        # cross-sections: west-east at two y, south-north at two x, vertical exaggeration 3
        zmin, zmax = Z_BOTTOM, 2400
        for tag, fixed, along in (("we_y6000", 6000, "x"), ("we_y16500", 16500, "x"), ("sn_x8000", 8000, "y"), ("sn_x22000", 22000, "y")):
            wpx = 900
            hpx = int((zmax - zmin) / (W / wpx) * 3)
            img = bytearray(b"\xff" * (wpx * hpx * 3))
            for px_ in range(wpx):
                t = (px_ + 0.5) * W / wpx
                x, y = (t, fixed) if along == "x" else (fixed, t)
                col = column(x, y)
                for py_ in range(hpx):
                    z = zmax - (py_ + 0.5) * (zmax - zmin) / hpx
                    if z <= col[2]:
                        img[(py_ * wpx + px_) * 3:(py_ * wpx + px_) * 3 + 3] = bytes(PALETTE[unit_at(col, z)])
            png(f"{preview}/section_{tag}.png", wpx, hpx, img)
        render_iso(cols, nb, zc0, f"{preview}/block_diagram.png")
        print("preview written")


if __name__ == "__main__":
    args = sys.argv[1:]
    main(args[0], args[args.index("--preview") + 1] if "--preview" in args else None)
