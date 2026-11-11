import math
from collections import defaultdict


class SpatialCorridorIndex:
    """
    Deterministic, high-speed spatial grid index for vehicle route corridors.
    Provides ~500m cell resolution for TRIPPY_POOL and ~5000m for ENTROPIC_FREIGHT.
    Falls back gracefully without external C-extensions.
    """

    def __init__(self):
        self.vehicles = {}
        self.cells = defaultdict(set)

    def _get_cell_size(self, platform: str) -> float:
        if platform == "TRIPPY_POOL":
            return 500.0
        return 5000.0

    def _coord_to_cell(self, x: float, y: float, cell_size: float) -> tuple[int, int]:
        return int(math.floor(x / cell_size)), int(math.floor(y / cell_size))

    def index_vehicle(self, vehicle_id: str, stops_coords: list, platform: str):
        cell_size = self._get_cell_size(platform)
        self.vehicles[vehicle_id] = {
            "coords": stops_coords,
            "platform": platform,
            "cell_size": cell_size,
        }

        # Index all coordinate points and discretized segments between consecutive points
        for i in range(len(stops_coords)):
            x0, y0 = stops_coords[i]
            c = self._coord_to_cell(x0, y0, cell_size)
            self.cells[c].add(vehicle_id)

            if i + 1 < len(stops_coords):
                x1, y1 = stops_coords[i + 1]
                dist = math.hypot(x1 - x0, y1 - y0)
                steps = max(1, int(math.ceil(dist / (cell_size * 0.5))))
                for step in range(1, steps):
                    inter_x = x0 + (x1 - x0) * (step / steps)
                    inter_y = y0 + (y1 - y0) * (step / steps)
                    self.cells[self._coord_to_cell(inter_x, inter_y, cell_size)].add(vehicle_id)

    def query_candidates(self, pickup_coord: tuple, platform: str, radius_m: float = 3500.0) -> list:
        cell_size = self._get_cell_size(platform)
        px, py = pickup_coord

        cell_radius = int(math.ceil(radius_m / cell_size))
        center_cx, center_cy = self._coord_to_cell(px, py, cell_size)

        candidate_ids = set()
        for dcx in range(-cell_radius, cell_radius + 1):
            for dcy in range(-cell_radius, cell_radius + 1):
                candidate_ids.update(self.cells.get((center_cx + dcx, center_cy + dcy), set()))

        # Precise segment distance check
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
