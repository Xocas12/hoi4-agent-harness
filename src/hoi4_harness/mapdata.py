"""The map as data: where each province and state is, in map pixels.

Unit orders (#85) are clicks on the map, and a click needs a coordinate. The
game keeps its geography in a few plain files, so the harness reads them:

* ``map/provinces.bmp`` -- one flat colour per province, 24-bit uncompressed;
* ``map/definition.csv`` -- colour -> province id, type (land/sea/lake) and terrain;
* ``map/adjacencies.csv`` -- straits and other crossings that share no border;
* ``history/states`` -- which provinces make up each state.

The same playset layering as the identifier index applies: a mod's file at the
same relative path replaces the game's, and ``replace_path`` drops the folder.
``map/default.map`` names the three map files, and a mod may rename them.

Coordinates here are *image* coordinates: x grows right and y grows DOWN from
the top-left corner, as in any image viewer and as on the screen. A BMP is
normally stored bottom-up (its first row is the bottom of the picture); the
reader undoes that, so ``y`` is never the file row.

The first pass over the picture takes a few seconds in pure Python, so its
result is cached as JSON, keyed by the map files' names, sizes and mtimes.
Interior points are computed on demand, from the rows a state covers.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
from array import array
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import groupby
from pathlib import Path

from .identifiers import Playset, _as_list, _parse

CACHE_VERSION = 1


class MapError(ValueError):
    """The map files are missing or not what the reader understands."""


# --- the picture --------------------------------------------------------------


class Bmp24:
    """An uncompressed 24-bit BMP. Pixels are read as packed ``r<<16|g<<8|b``
    integers, one row at a time, top row first however the file is stored."""

    def __init__(self, data: bytes):
        if data[:2] != b"BM" or len(data) < 54:
            raise MapError("not a BMP file")
        (self.offset,) = struct.unpack_from("<I", data, 10)
        header, width, height, _planes, bpp, compression = struct.unpack_from("<IiiHHI", data, 14)
        if header < 40:
            raise MapError("unsupported BMP header (need BITMAPINFOHEADER)")
        if bpp != 24 or compression != 0:
            raise MapError(f"need an uncompressed 24-bit BMP, got {bpp} bpp, compression {compression}")
        if width <= 0 or height == 0:
            raise MapError("empty BMP")
        self.width = width
        self.bottom_up = height > 0
        self.height = abs(height)
        self.stride = (width * 3 + 3) & ~3  # rows are padded to 4 bytes
        if len(data) < self.offset + self.stride * self.height:
            raise MapError("BMP pixel data is truncated")
        self._data = data

    @classmethod
    def open(cls, path: Path) -> Bmp24:
        return cls(Path(path).read_bytes())

    def row(self, y: int) -> array:
        """Row ``y`` (0 = top of the picture) as packed ``0xRRGGBB`` ints."""
        if not 0 <= y < self.height:
            raise IndexError(y)
        file_row = self.height - 1 - y if self.bottom_up else y
        start = self.offset + file_row * self.stride
        raw = self._data[start:start + self.width * 3]
        buf = bytearray(self.width * 4)
        buf[0::4] = raw[0::3]  # blue
        buf[1::4] = raw[1::3]  # green
        buf[2::4] = raw[2::3]  # red
        out = array("I")
        out.frombytes(bytes(buf))
        if sys.byteorder == "big":
            out.byteswap()
        return out

    def pixel(self, x: int, y: int) -> int:
        return self.row(y)[x]


# --- provinces ----------------------------------------------------------------


@dataclass
class Province:
    id: int
    kind: str                      # land / sea / lake
    terrain: str
    coastal: bool
    colour: int                    # packed r<<16|g<<8|b
    pixels: int = 0
    centroid: tuple[float, float] = (0.0, 0.0)
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)   # x0, y0, x1, y1 inclusive

    @property
    def centroid_pixel(self) -> tuple[int, int]:
        return round(self.centroid[0]), round(self.centroid[1])


@dataclass
class State:
    id: int
    name_key: str
    provinces: list[int]
    owner: str | None = None


def read_definitions(text: str) -> dict[int, Province]:
    """``id;r;g;b;type;coastal;terrain;continent`` (id 0 is the unused black)."""
    provinces: dict[int, Province] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cols = line.split(";")
        if len(cols) < 7:
            continue
        try:
            pid, r, g, b = (int(c) for c in cols[:4])
        except ValueError:
            continue  # the header row, if there is one
        provinces[pid] = Province(
            id=pid, kind=cols[4].strip() or "land", terrain=cols[6].strip(),
            coastal=cols[5].strip().lower() == "true", colour=(r << 16) | (g << 8) | b,
        )
    return provinces


def read_adjacencies(text: str) -> list[tuple[int, int, str]]:
    """The straits and crossings: ``(from, to, type)``. Comment lines and the
    ``-1;-1;...`` end marker are skipped."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.lower().startswith("from;"):
            continue
        cols = line.split(";")
        try:
            a, b = int(cols[0]), int(cols[1])
        except (ValueError, IndexError):
            continue
        if a < 0 or b < 0:
            continue
        out.append((a, b, cols[2].strip() if len(cols) > 2 else ""))
    return out


def scan_picture(bmp: Bmp24, by_colour: dict[int, int]):
    """One pass over the picture: per-province pixel count, centroid sums and
    bounding box, plus the pairs of provinces that touch.

    Runs of equal colour do the arithmetic (C-speed ``groupby``); a row's
    touching pairs come from zipping it against itself shifted by one and
    against the row above, which de-duplicates in C as well. Touching means
    sharing an edge, not a corner.
    """
    count: dict[int, int] = defaultdict(int)
    sum_x2: dict[int, int] = defaultdict(int)   # twice the sum of x
    sum_y: dict[int, int] = defaultdict(int)
    box: dict[int, list[int]] = {}
    pairs: set[tuple[int, int]] = set()
    unknown: dict[int, int] = defaultdict(int)
    prev: list[int] | None = None
    for y in range(bmp.height):
        cur = bmp.row(y).tolist()
        x = 0
        for colour, run in groupby(cur):
            n = sum(1 for _ in run)
            pid = by_colour.get(colour)
            if pid is None:
                unknown[colour] += n
            else:
                count[pid] += n
                sum_x2[pid] += n * (2 * x + n - 1)
                sum_y[pid] += n * y
                b = box.get(pid)
                if b is None:
                    box[pid] = [x, y, x + n - 1, y]
                else:
                    if x < b[0]:
                        b[0] = x
                    if x + n - 1 > b[2]:
                        b[2] = x + n - 1
                    b[3] = y
            x += n
        pairs.update(zip(cur, cur[1:], strict=False))
        if prev is not None:
            pairs.update(zip(cur, prev, strict=True))
        prev = cur
    edges = set()
    for a, b in pairs:
        if a != b and a in by_colour and b in by_colour:
            ia, ib = by_colour[a], by_colour[b]
            edges.add((ia, ib) if ia < ib else (ib, ia))
    return count, sum_x2, sum_y, box, edges, unknown


# --- the layer ----------------------------------------------------------------


def resolve_file(playset: Playset, rel: str) -> Path | None:
    """The file the game would load at ``rel``: the last playset layer that has
    it, ignoring layers a later ``replace_path`` dropped."""
    found: Path | None = None
    folder = os.path.dirname(rel)
    for root, replace in [(playset.game_dir, [])] + [(m.path, m.replace_paths) for m in playset.mods]:
        for replaced in replace:
            if folder == replaced or folder.startswith(replaced + "/"):
                found = None
        candidate = Path(root) / rel
        if candidate.is_file():
            found = candidate
    return found


def _default_map_names(playset: Playset) -> dict[str, str]:
    names = {"provinces": "provinces.bmp", "definitions": "definition.csv",
             "adjacencies": "adjacencies.csv"}
    path = resolve_file(playset, "map/default.map")
    if path is not None:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for line in text.splitlines():
            line = line.split("#", 1)[0]
            if "=" in line:
                key, value = (s.strip() for s in line.split("=", 1))
                if key in names:
                    names[key] = value.strip('"')
    return names


def default_cache_dir() -> Path:
    if os.environ.get("HOI4_MAP_CACHE"):
        return Path(os.environ["HOI4_MAP_CACHE"])
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "hoi4-harness" / "map"
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "hoi4-harness" / "map"


@dataclass
class MapLayer:
    """Provinces, adjacency and states for one playset."""

    width: int
    height: int
    provinces: dict[int, Province]
    adjacent: dict[int, set[int]]      # shared pixel borders
    straits: dict[int, set[int]]       # adjacencies.csv crossings
    states: dict[int, State] = field(default_factory=dict)
    cache_key: str = ""
    from_cache: bool = False
    _bmp: Bmp24 | None = field(default=None, repr=False)
    _bmp_path: Path | None = field(default=None, repr=False)
    _state_of: dict[int, int] = field(default_factory=dict, repr=False)
    _interior: dict[tuple[str, int], tuple[int, int]] = field(default_factory=dict, repr=False)

    # --- loading --------------------------------------------------------------

    @classmethod
    def load(cls, playset: Playset, cache_dir: Path | None | bool = None) -> MapLayer:
        """Read the playset's map. ``cache_dir`` False disables the cache; None
        uses :func:`default_cache_dir`."""
        names = _default_map_names(playset)
        files = {kind: resolve_file(playset, f"map/{name}") for kind, name in names.items()}
        for kind in ("provinces", "definitions"):
            if files[kind] is None:
                raise MapError(f"map/{names[kind]} not found in the playset "
                               f"(looked in {playset.game_dir} and {len(playset.mods)} mod(s))")
        key = _cache_key(playset, files)
        cache_path = None
        if cache_dir is not False:
            cache_path = Path(cache_dir) if cache_dir else default_cache_dir()
            cache_path = cache_path / f"map-{key}.json"
        layer = _from_cache(cache_path) if cache_path else None
        if layer is None:
            layer = _scan(files)
            if cache_path:
                _write_cache(cache_path, layer, key)
        layer.cache_key = key
        layer._bmp_path = files["provinces"]
        layer._read_states(playset)
        return layer

    def _read_states(self, playset: Playset) -> None:
        for path in playset.layered("history/states"):
            for block in _as_list(_parse(path).get("state")):
                if not isinstance(block, dict) or "id" not in block:
                    continue
                try:
                    sid = int(str(block["id"]).strip())
                except ValueError:
                    continue
                owned = block.get("provinces") or {}
                items = owned.get("_items", []) if isinstance(owned, dict) else []
                ids = []
                for item in items:
                    try:
                        ids.append(int(item))
                    except (TypeError, ValueError):
                        pass
                history = _as_list(block.get("history"))
                owner = history[0].get("owner") if history and isinstance(history[0], dict) else None
                self.states[sid] = State(sid, str(block.get("name", f"STATE_{sid}")), ids,
                                         owner if isinstance(owner, str) else None)
        self._state_of = {p: s.id for s in self.states.values() for p in s.provinces}

    # --- queries --------------------------------------------------------------

    def neighbours(self, province: int, straits: bool = True) -> list[int]:
        out = set(self.adjacent.get(province, ()))
        if straits:
            out |= self.straits.get(province, set())
        return sorted(out)

    def state_of(self, province: int) -> int | None:
        return self._state_of.get(province)

    def picture(self) -> Bmp24:
        if self._bmp is None:
            if self._bmp_path is None:
                raise MapError("this layer has no picture to read")
            self._bmp = Bmp24.open(self._bmp_path)
        return self._bmp

    def province_at(self, x: int, y: int) -> int | None:
        """The province under map pixel (x, y), or None off the map or on a
        colour the definitions do not know."""
        bmp = self.picture()
        if not (0 <= x < bmp.width and 0 <= y < bmp.height):
            return None
        return self._by_colour().get(bmp.pixel(x, y))

    def _by_colour(self) -> dict[int, int]:
        return {p.colour: p.id for p in self.provinces.values()}

    def state_bbox(self, state: int) -> tuple[int, int, int, int]:
        boxes = [self.provinces[p].bbox for p in self._state(state).provinces
                 if p in self.provinces and self.provinces[p].pixels]
        if not boxes:
            raise MapError(f"state {state} has no province on the map")
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    def state_centroid(self, state: int) -> tuple[float, float]:
        """Pixel-weighted centroid of the state. May lie outside it."""
        total = sx = sy = 0.0
        for p in self._state(state).provinces:
            prov = self.provinces.get(p)
            if prov and prov.pixels:
                total += prov.pixels
                sx += prov.centroid[0] * prov.pixels
                sy += prov.centroid[1] * prov.pixels
        if not total:
            raise MapError(f"state {state} has no province on the map")
        return sx / total, sy / total

    def state_pixels(self, state: int) -> int:
        return sum(self.provinces[p].pixels for p in self._state(state).provinces
                   if p in self.provinces)

    def _state(self, state: int) -> State:
        try:
            return self.states[state]
        except KeyError:
            raise MapError(f"no state {state} in this playset") from None

    # --- the click point ------------------------------------------------------

    def interior_point(self, kind: str, ident: int) -> tuple[int, int]:
        """A pixel certainly inside the province (``kind='province'``) or state
        (``'state'``): the one farthest from anything outside it, ties broken
        toward the centroid. The centroid of a crescent or an archipelago can
        sit in the sea; this cannot."""
        cached = self._interior.get((kind, ident))
        if cached:
            return cached
        if kind == "province":
            prov = self.provinces.get(ident)
            if prov is None or not prov.pixels:
                raise MapError(f"province {ident} is not on the map")
            colours = {prov.colour}
            x0, y0, x1, y1 = prov.bbox
            centre = prov.centroid
        elif kind == "state":
            colours = {self.provinces[p].colour for p in self._state(ident).provinces
                       if p in self.provinces and self.provinces[p].pixels}
            x0, y0, x1, y1 = self.state_bbox(ident)
            centre = self.state_centroid(ident)
        else:
            raise ValueError(kind)
        bmp = self.picture()
        mask = [[1 if c in colours else 0 for c in bmp.row(y)[x0:x1 + 1]]
                for y in range(y0, y1 + 1)]
        mx, my = _deepest(mask, centre[0] - x0, centre[1] - y0)
        point = (x0 + mx, y0 + my)
        self._interior[(kind, ident)] = point
        return point


def _deepest(mask: list[list[int]], cx: float, cy: float) -> tuple[int, int]:
    """The mask pixel with the largest chamfer (3-4) distance to the outside.
    Outside includes beyond the box. Ties go to the one nearest (cx, cy)."""
    h, w = len(mask), len(mask[0])
    big = 1 << 30
    dist = [[big if v else 0 for v in row] for row in mask]
    for y in range(h):
        row = dist[y]
        up = dist[y - 1] if y else None
        for x in range(w):
            if not row[x]:
                continue
            d = 3 if (x == 0 or y == 0) else big
            if x:
                d = min(d, row[x - 1] + 3)
            if up is not None:
                d = min(d, up[x] + 3)
                if x:
                    d = min(d, up[x - 1] + 4)
                if x + 1 < w:
                    d = min(d, up[x + 1] + 4)
            row[x] = d
    for y in range(h - 1, -1, -1):
        row = dist[y]
        down = dist[y + 1] if y + 1 < h else None
        for x in range(w - 1, -1, -1):
            if not row[x]:
                continue
            d = row[x]
            if x + 1 == w or y + 1 == h:
                d = min(d, 3)
            if x + 1 < w:
                d = min(d, row[x + 1] + 3)
            if down is not None:
                d = min(d, down[x] + 3)
                if x + 1 < w:
                    d = min(d, down[x + 1] + 4)
                if x:
                    d = min(d, down[x - 1] + 4)
            row[x] = d
    best = 0
    ties: list[tuple[int, int]] = []
    for y in range(h):
        row = dist[y]
        for x in range(w):
            if row[x] > best:
                best, ties = row[x], [(x, y)]
            elif row[x] == best and best:
                ties.append((x, y))
    if not ties:
        raise MapError("empty region")
    return min(ties, key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)


# --- scan + cache -------------------------------------------------------------


def _scan(files: dict[str, Path | None]) -> MapLayer:
    provinces = read_definitions(files["definitions"].read_text(encoding="utf-8-sig", errors="replace"))
    by_colour = {p.colour: p.id for p in provinces.values() if p.id != 0}
    bmp = Bmp24.open(files["provinces"])
    count, sum_x2, sum_y, box, edges, _unknown = scan_picture(bmp, by_colour)
    for pid, n in count.items():
        prov = provinces[pid]
        prov.pixels = n
        prov.centroid = (sum_x2[pid] / (2 * n), sum_y[pid] / n)
        prov.bbox = tuple(box[pid])  # type: ignore[assignment]
    adjacent: dict[int, set[int]] = defaultdict(set)
    for a, b in edges:
        adjacent[a].add(b)
        adjacent[b].add(a)
    straits: dict[int, set[int]] = defaultdict(set)
    if files.get("adjacencies") is not None:
        text = files["adjacencies"].read_text(encoding="utf-8-sig", errors="replace")
        for a, b, _kind in read_adjacencies(text):
            if a in provinces and b in provinces and b not in adjacent[a]:
                straits[a].add(b)
                straits[b].add(a)
    return MapLayer(bmp.width, bmp.height, provinces, dict(adjacent), dict(straits))


def _cache_key(playset: Playset, files: dict[str, Path | None]) -> str:
    digest = hashlib.sha1(f"v{CACHE_VERSION}".encode())
    for kind in sorted(files):
        path = files[kind]
        if path is None:
            digest.update(f"{kind}:-".encode())
            continue
        st = path.stat()
        digest.update(f"{kind}:{path.name}:{st.st_size}:{st.st_mtime_ns}".encode())
    digest.update(playset.name.encode())  # the stats carry the rest of the playset
    return digest.hexdigest()[:16]


def _write_cache(path: Path, layer: MapLayer, key: str) -> None:
    payload = {
        "version": CACHE_VERSION, "key": key, "width": layer.width, "height": layer.height,
        "provinces": [[p.id, p.kind, p.terrain, int(p.coastal), p.colour, p.pixels,
                       p.centroid[0], p.centroid[1], list(p.bbox)]
                      for p in layer.provinces.values()],
        "adjacent": {str(k): sorted(v) for k, v in layer.adjacent.items()},
        "straits": {str(k): sorted(v) for k, v in layer.straits.items()},
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # a cache that cannot be written is only slower


def _from_cache(path: Path) -> MapLayer | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != CACHE_VERSION:
            return None
        provinces = {}
        for pid, kind, terrain, coastal, colour, pixels, cx, cy, bbox in data["provinces"]:
            provinces[pid] = Province(pid, kind, terrain, bool(coastal), colour, pixels,
                                      (cx, cy), tuple(bbox))
        layer = MapLayer(
            data["width"], data["height"], provinces,
            {int(k): set(v) for k, v in data["adjacent"].items()},
            {int(k): set(v) for k, v in data["straits"].items()},
        )
        layer.from_cache = True
        return layer
    except (OSError, ValueError, KeyError, TypeError):
        return None
