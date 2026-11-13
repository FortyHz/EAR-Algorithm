import math
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple, Union
import h3


class SpatialCorridorIndex:
    """
    Production Uber H3 hexagonal spatial trajectory corridor indexer.
    - TRIPPY_POOL: Urban Resolution 8 (~460m cell diameter).
    - ENTROPIC_FREIGHT: Intercity Resolution 6 (~3.6km cell diameter).
    - Backward-compatible with planar (x, y) coordinates as well as GPS (lat, lng).
    - Supports index_vehicle_corridor & query_nearby_candidates (GIS Sandbox contract)
      AND index_vehicle & query_candidates (EAR pipeline contract).
    """

    RESOLUTIONS = {
        "TRIPPY_POOL": 8,
        "ENTROPIC_FREIGHT": 6,
    }

    INTERPOLATION_STEP_M = {
        8: 250.0,   # Resolution 8 diameter ~460m
        6: 1500.0,  # Resolution 6 diameter ~3600m
    }

    def __init__(self):
        # Uber H3 inverted index: platform -> h3_cell -> set of vehicle_ids
        self._h3_index: Dict[str, Dict[str, Set[str]]] = {
            "TRIPPY_POOL": {},
            "ENTROPIC_FREIGHT": {},
        }
        self._vehicle_cells: Dict[str, Dict[str, Set[str]]] = {
            "TRIPPY_POOL": {},
            "ENTROPIC_FREIGHT": {},
        }

        # Planar fallback index for Euclidean (x, y) coordinates (harness benchmarks)
        self.vehicles = {}
        self.planar_cells = defaultdict(set)

    @staticmethod
    def _is_gps(coord: Tuple[float, float]) -> bool:
        """
        Detects whether a coordinate is GPS (lat, lng) vs planar meters (x, y).
        GPS lat in [-90, 90] and lng in [-180, 180].
        Planar coordinates in benchmarks are in meters (e.g. 2500, 3100, 50000).
        """
        c1, c2 = coord
        return -90.0 <= c1 <= 90.0 and -180.0 <= c2 <= 180.0 and not (abs(c1) < 0.0001 and abs(c2) < 0.0001)

    def _get_resolution(self, platform: str) -> int:
        return self.RESOLUTIONS.get(platform, 8)

    @staticmethod
    def _haversine_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        r = 6371000.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lng2 - lng1)
        a = (math.sin(dphi / 2.0) ** 2 +
             math.cos(phi1) * math.cos(phi2) * (math.sin(dlam / 2.0) ** 2))
        return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

    # --- Uber H3 Contract ---

    def index_vehicle_corridor(
        self,
        vehicle_id: str,
        route_coords: List[Tuple[float, float]],
        platform: str,
    ) -> Set[str]:
        if not route_coords:
            return set()

        resolution = self._get_resolution(platform)
        step_m = self.INTERPOLATION_STEP_M.get(resolution, 250.0)
        indexed_cells: Set[str] = set()

        if len(route_coords) == 1:
            lat, lng = route_coords[0]
            indexed_cells.add(h3.latlng_to_cell(lat, lng, resolution))
        else:
            for idx in range(len(route_coords) - 1):
                lat1, lng1 = route_coords[idx]
                lat2, lng2 = route_coords[idx + 1]

                seg_dist = self._haversine_distance(lat1, lng1, lat2, lng2)
                num_steps = max(1, math.ceil(seg_dist / step_m))

                for step in range(num_steps):
                    fraction = step / float(num_steps)
                    interp_lat = lat1 + fraction * (lat2 - lat1)
                    interp_lng = lng1 + fraction * (lng2 - lng1)
                    indexed_cells.add(h3.latlng_to_cell(interp_lat, interp_lng, resolution))

            last_lat, last_lng = route_coords[-1]
            indexed_cells.add(h3.latlng_to_cell(last_lat, last_lng, resolution))

        platform_idx = self._h3_index.setdefault(platform, {})
        veh_store = self._vehicle_cells.setdefault(platform, {})

        prior_cells = veh_store.get(vehicle_id)
        if prior_cells:
            for cell in prior_cells:
                cell_vehs = platform_idx.get(cell)
                if cell_vehs and vehicle_id in cell_vehs:
                    cell_vehs.remove(vehicle_id)
                    if not cell_vehs:
                        del platform_idx[cell]

        for cell in indexed_cells:
            if cell not in platform_idx:
                platform_idx[cell] = set()
            platform_idx[cell].add(vehicle_id)

        veh_store[vehicle_id] = indexed_cells
        return indexed_cells

    def query_nearby_candidates(
        self,
        pickup_lat: float,
        pickup_lng: float,
        platform: str,
        k_ring: int = 2,
    ) -> Set[str]:
        resolution = self._get_resolution(platform)
        center_cell = h3.latlng_to_cell(pickup_lat, pickup_lng, resolution)
        neighborhood = h3.grid_disk(center_cell, k_ring)

        platform_idx = self._h3_index.get(platform, {})
        candidates: Set[str] = set()

        for cell in neighborhood:
            vehs = platform_idx.get(cell)
            if vehs:
                candidates.update(vehs)

        return candidates

    # --- Backward-Compatible Dispatch Pipeline Contract ---

    def _get_planar_cell_size(self, platform: str) -> float:
        if platform == "TRIPPY_POOL":
            return 500.0
        return 5000.0

    def _coord_to_planar_cell(self, x: float, y: float, cell_size: float) -> Tuple[int, int]:
        return int(math.floor(x / cell_size)), int(math.floor(y / cell_size))

    def index_vehicle(self, vehicle_id: str, stops_coords: list, platform: str):
        """Indexes vehicle route for dispatch pipeline (handles GPS or planar)."""
        is_gps_mode = stops_coords and all(self._is_gps(pt) for pt in stops_coords)

        self.vehicles[vehicle_id] = {
            "coords": stops_coords,
            "platform": platform,
            "is_gps": is_gps_mode,
        }

        if is_gps_mode:
            self.index_vehicle_corridor(vehicle_id, stops_coords, platform)
            return

        # Planar mode (meters)
        cell_size = self._get_planar_cell_size(platform)
        for i in range(len(stops_coords)):
            x0, y0 = stops_coords[i]
            c = self._coord_to_planar_cell(x0, y0, cell_size)
            self.planar_cells[c].add(vehicle_id)

            if i + 1 < len(stops_coords):
                x1, y1 = stops_coords[i + 1]
                dist = math.hypot(x1 - x0, y1 - y0)
                steps = max(1, int(math.ceil(dist / (cell_size * 0.5))))
                for step in range(1, steps):
                    inter_x = x0 + (x1 - x0) * (step / steps)
                    inter_y = y0 + (y1 - y0) * (step / steps)
                    self.planar_cells[self._coord_to_planar_cell(inter_x, inter_y, cell_size)].add(vehicle_id)

    def query_candidates(self, pickup_coord: tuple, platform: str, radius_m: float = 3500.0) -> list:
        """Queries nearby candidate vehicle IDs for pickup coordinate."""
        is_gps_mode = self._is_gps(pickup_coord)

        if is_gps_mode:
            lat, lng = pickup_coord
            # Approximate k-ring: Resolution 8 radius ~460m -> k=ceil(radius_m/460)
            resolution = self._get_resolution(platform)
            cell_radius_m = 460.0 if resolution == 8 else 3600.0
            k_ring = max(1, int(math.ceil(radius_m / cell_radius_m)))
            candidates = self.query_nearby_candidates(lat, lng, platform, k_ring=k_ring)
            return list(candidates)

        # Planar mode
        cell_size = self._get_planar_cell_size(platform)
        px, py = pickup_coord

        cell_radius = int(math.ceil(radius_m / cell_size))
        center_cx, center_cy = self._coord_to_planar_cell(px, py, cell_size)

        candidate_ids = set()
        for dcx in range(-cell_radius, cell_radius + 1):
            for dcy in range(-cell_radius, cell_radius + 1):
                candidate_ids.update(self.planar_cells.get((center_cx + dcx, center_cy + dcy), set()))

        filtered = []
        for veh_id in candidate_ids:
            coords = self.vehicles[veh_id]["coords"]
            min_dist = float("inf")
            for i in range(len(coords)):
                x0, y0 = coords[i]
                d = math.hypot(px - x0, py - y0)
                if d < min_dist:
                    min_dist = d

                if i + 1 < len(coords):
                    x1, y1 = coords[i + 1]
                    dx = x1 - x0
                    dy = y1 - y0
                    seg_len_sq = dx * dx + dy * dy
                    if seg_len_sq > 0:
                        t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / seg_len_sq))
                        proj_x = x0 + t * dx
                        proj_y = y0 + t * dy
                        proj_d = math.hypot(px - proj_x, py - proj_y)
                        if proj_d < min_dist:
                            min_dist = proj_d

            if min_dist <= radius_m:
                filtered.append(veh_id)

        return filtered
