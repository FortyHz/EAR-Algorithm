import copy
import json
import sqlite3
import threading
from typing import Any, List, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.adapters.matrix_provider import get_route_polyline
from src.api.dispatch_pipeline import process_live_dispatch

router = APIRouter(prefix="/api/v1/freight", tags=["Freight Lifecycle"])

# Thread-safe in-memory SQLite database
_DB_LOCK = threading.Lock()
_CONN = sqlite3.connect(":memory:", check_same_thread=False)
_CONN.row_factory = sqlite3.Row


def _init_db():
    with _DB_LOCK:
        cur = _CONN.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS carriers (
                vehicle_id TEXT PRIMARY KEY,
                capacity_max REAL NOT NULL,
                current_lat REAL NOT NULL,
                current_lng REAL NOT NULL,
                confirmed_onboard_load REAL NOT NULL DEFAULT 0.0,
                active_stops_json TEXT NOT NULL DEFAULT '[]'
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS loads (
                load_id TEXT PRIMARY KEY,
                origin_name TEXT,
                dest_name TEXT,
                pickup_lat REAL,
                pickup_lng REAL,
                dropoff_lat REAL,
                dropoff_lng REAL,
                weight_tons REAL,
                pickup_latest_sec REAL,
                dropoff_latest_sec REAL,
                status TEXT NOT NULL DEFAULT 'OPEN',
                assigned_vehicle_id TEXT,
                manifest_stops_json TEXT
            )
        """)
        cur.execute("PRAGMA table_info(loads)")
        cols = [c[1] for c in cur.fetchall()]
        if "manifest_stops_json" not in cols:
            cur.execute("ALTER TABLE loads ADD COLUMN manifest_stops_json TEXT")
        _CONN.commit()


_init_db()


def _compute_route_coordinates(start_lat: float, start_lng: float, stops: list) -> list[list[float]]:
    waypoints = [[float(start_lat), float(start_lng)]]
    for s in stops:
        lat = s.get("lat") if "lat" in s else s.get("x", 0.0)
        lng = s.get("lng") if "lng" in s else s.get("y", 0.0)
        waypoints.append([float(lat), float(lng)])
    return get_route_polyline(waypoints)


class RegisterCarrierPayload(BaseModel):
    vehicle_id: str
    capacity_max: float
    current_lat: float
    current_lng: float
    confirmed_onboard_load: float = 0.0
    active_stops: list[dict[str, Any]] = Field(default_factory=list)


class CreateLoadPayload(BaseModel):
    load_id: str
    origin_name: Optional[str] = None
    dest_name: Optional[str] = None
    pickup_lat: Optional[float] = None
    pickup_lng: Optional[float] = None
    dropoff_lat: Optional[float] = None
    dropoff_lng: Optional[float] = None
    weight_tons: Optional[float] = None
    pickup_latest_sec: Optional[float] = None
    dropoff_latest_sec: Optional[float] = None
    status: str = "OPEN"
    manifest_stops: Optional[list[dict[str, Any]]] = None


class AcceptLoadPayload(BaseModel):
    vehicle_id: Optional[str] = None


# [FUSION-HOOK]: POST /api/v1/freight/carriers/register -> Register or reset carrier telematics, capacity, and active stops
@router.post("/carriers/register")
def register_carrier(payload: RegisterCarrierPayload) -> dict[str, Any]:
    with _DB_LOCK:
        cur = _CONN.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO carriers (
                vehicle_id, capacity_max, current_lat, current_lng, confirmed_onboard_load, active_stops_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                payload.vehicle_id,
                float(payload.capacity_max),
                float(payload.current_lat),
                float(payload.current_lng),
                float(payload.confirmed_onboard_load),
                json.dumps(payload.active_stops),
            ),
        )
        _CONN.commit()

    return {
        "status": "REGISTERED",
        "carrier": {
            "vehicle_id": payload.vehicle_id,
            "capacity_max": payload.capacity_max,
            "current_lat": payload.current_lat,
            "current_lng": payload.current_lng,
            "confirmed_onboard_load": payload.confirmed_onboard_load,
            "active_stops": payload.active_stops,
        },
    }


# [FUSION-HOOK]: POST /api/v1/freight/loads -> Create shipper load order supporting single-leg and multi-stop manifest stops
@router.post("/loads")
def create_load(payload: CreateLoadPayload) -> dict[str, Any]:
    manifest_stops = payload.manifest_stops
    if manifest_stops is None or len(manifest_stops) == 0:
        # Auto-convert legacy single pickup/dropoff into 2-stop manifest_stops list
        orig = payload.origin_name or f"Pickup_{payload.load_id}"
        dest = payload.dest_name or f"Dropoff_{payload.load_id}"
        plat = float(payload.pickup_lat if payload.pickup_lat is not None else 0.0)
        plng = float(payload.pickup_lng if payload.pickup_lng is not None else 0.0)
        dlat = float(payload.dropoff_lat if payload.dropoff_lat is not None else 0.0)
        dlng = float(payload.dropoff_lng if payload.dropoff_lng is not None else 0.0)
        w = float(payload.weight_tons if payload.weight_tons is not None else 0.0)
        pt = float(payload.pickup_latest_sec if payload.pickup_latest_sec is not None else 3600.0)
        dt = float(payload.dropoff_latest_sec if payload.dropoff_latest_sec is not None else 7200.0)

        manifest_stops = [
            {
                "name": orig,
                "type": "PICKUP",
                "lat": plat,
                "lng": plng,
                "weight_delta_tons": w,
                "latest_time_sec": pt,
            },
            {
                "name": dest,
                "type": "DROPOFF",
                "lat": dlat,
                "lng": dlng,
                "weight_delta_tons": -w,
                "latest_time_sec": dt,
            },
        ]
        origin_name = orig
        dest_name = dest
        pickup_lat = plat
        pickup_lng = plng
        dropoff_lat = dlat
        dropoff_lng = dlng
        weight_tons = w
        pickup_latest_sec = pt
        dropoff_latest_sec = dt
    else:
        # Populate legacy fields from manifest stops
        pickup_stops = [s for s in manifest_stops if (s.get("type", "").upper() == "PICKUP" or s.get("is_pickup"))]
        dropoff_stops = [s for s in manifest_stops if (s.get("type", "").upper() == "DROPOFF" or s.get("is_pickup") is False)]
        first_p = pickup_stops[0] if pickup_stops else manifest_stops[0]
        last_d = dropoff_stops[-1] if dropoff_stops else manifest_stops[-1]

        for s in manifest_stops:
            s_n = s.get("name") or s.get("label") or "stop"
            s["name"] = s_n
            s["label"] = s.get("label") or s_n
            s["stop_id"] = s.get("stop_id") or s_n
            is_p = (s.get("type", "").upper() == "PICKUP") if "type" in s else s.get("is_pickup", True)
            s["is_pickup"] = is_p
            s["type"] = "PICKUP" if is_p else "DROPOFF"
            s_lt = float(s.get("lat") if "lat" in s else s.get("x", 0.0))
            s_lg = float(s.get("lng") if "lng" in s else s.get("y", 0.0))
            s["lat"] = s_lt
            s["lng"] = s_lg
            s["x"] = s_lt
            s["y"] = s_lg
            if "weight_delta_tons" in s and s["weight_delta_tons"] is not None:
                w_v = float(s["weight_delta_tons"])
                s["weight_delta_tons"] = w_v
                s["load_change"] = w_v
            elif "load_change" in s and s["load_change"] is not None:
                l_v = float(s["load_change"])
                s["load_change"] = l_v
                s["weight_delta_tons"] = l_v

        origin_name = payload.origin_name or first_p.get("name") or first_p.get("label") or "Origin"
        dest_name = payload.dest_name or last_d.get("name") or last_d.get("label") or "Dest"
        pickup_lat = float(payload.pickup_lat if payload.pickup_lat is not None else (first_p.get("lat") if "lat" in first_p else first_p.get("x", 0.0)))
        pickup_lng = float(payload.pickup_lng if payload.pickup_lng is not None else (first_p.get("lng") if "lng" in first_p else first_p.get("y", 0.0)))
        dropoff_lat = float(payload.dropoff_lat if payload.dropoff_lat is not None else (last_d.get("lat") if "lat" in last_d else last_d.get("x", 0.0)))
        dropoff_lng = float(payload.dropoff_lng if payload.dropoff_lng is not None else (last_d.get("lng") if "lng" in last_d else last_d.get("y", 0.0)))
        pickup_latest_sec = float(payload.pickup_latest_sec if payload.pickup_latest_sec is not None else first_p.get("latest_time_sec", 3600.0))
        dropoff_latest_sec = float(payload.dropoff_latest_sec if payload.dropoff_latest_sec is not None else last_d.get("latest_time_sec", 7200.0))

        if payload.weight_tons is not None:
            weight_tons = float(payload.weight_tons)
        elif pickup_stops:
            weight_tons = float(sum(abs(float(s.get("weight_delta_tons", s.get("load_change", 0.0)))) for s in pickup_stops))
        else:
            weight_tons = 0.0

    with _DB_LOCK:
        cur = _CONN.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO loads (
                load_id, origin_name, dest_name, pickup_lat, pickup_lng, dropoff_lat, dropoff_lng,
                weight_tons, pickup_latest_sec, dropoff_latest_sec, status, assigned_vehicle_id, manifest_stops_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.load_id,
                origin_name,
                dest_name,
                pickup_lat,
                pickup_lng,
                dropoff_lat,
                dropoff_lng,
                weight_tons,
                pickup_latest_sec,
                dropoff_latest_sec,
                payload.status,
                None,
                json.dumps(manifest_stops),
            ),
        )
        _CONN.commit()

    load_dict = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    load_dict["manifest_stops"] = manifest_stops
    load_dict["origin_name"] = origin_name
    load_dict["dest_name"] = dest_name
    load_dict["weight_tons"] = weight_tons
    return {
        "status": "CREATED",
        "load": load_dict,
    }


# [FUSION-HOOK]: GET /api/v1/freight/carriers/{vehicle_id}/offers -> Fetch feasible dispatch offers partitioned into mid-route pool and backhaul with route polylines
@router.get("/carriers/{vehicle_id}/offers")
def get_carrier_offers(vehicle_id: str) -> dict[str, Any]:
    with _DB_LOCK:
        cur = _CONN.cursor()
        cur.execute("SELECT * FROM carriers WHERE vehicle_id = ?", (vehicle_id,))
        carrier_row = cur.fetchone()
        if not carrier_row:
            raise HTTPException(status_code=404, detail=f"Carrier {vehicle_id} not found")

        cur.execute("SELECT * FROM loads WHERE status = 'OPEN'")
        open_load_rows = cur.fetchall()

    carrier_stops = json.loads(carrier_row["active_stops_json"])
    carrier_vehicle = {
        "id": carrier_row["vehicle_id"],
        "capacity_max": float(carrier_row["capacity_max"]),
        "current_load": float(carrier_row["confirmed_onboard_load"]),
        "current_x": float(carrier_row["current_lat"]),
        "current_y": float(carrier_row["current_lng"]),
        "stops": carrier_stops,
    }

    mid_route_pool_offers = []
    next_leg_backhaul_offers = []

    for load_row in open_load_rows:
        manifest_stops = json.loads(load_row["manifest_stops_json"]) if ("manifest_stops_json" in load_row.keys() and load_row["manifest_stops_json"]) else None

        req = {
            "id": load_row["load_id"],
            "load": float(load_row["weight_tons"] or 0.0),
            "pickup": {
                "name": load_row["origin_name"] or "Origin",
                "x": float(load_row["pickup_lat"] or 0.0),
                "y": float(load_row["pickup_lng"] or 0.0),
                "latest_time_sec": float(load_row["pickup_latest_sec"] or 3600.0),
            },
            "dropoff": {
                "name": load_row["dest_name"] or "Dest",
                "x": float(load_row["dropoff_lat"] or 0.0),
                "y": float(load_row["dropoff_lng"] or 0.0),
                "latest_time_sec": float(load_row["dropoff_latest_sec"] or 7200.0),
            },
        }
        if manifest_stops:
            req["manifest_stops"] = manifest_stops

        dispatch_res = process_live_dispatch(
            fleet=[copy.deepcopy(carrier_vehicle)],
            request=req,
            platform="ENTROPIC_FREIGHT",
        )

        if dispatch_res.get("eligible_count", 0) > 0 and dispatch_res.get("ranked_candidates"):
            cand = dispatch_res["ranked_candidates"][0]
            cand_stops = cand.get("updated_stops", [])
            route_coords = _compute_route_coordinates(carrier_vehicle["current_x"], carrier_vehicle["current_y"], cand_stops)
            offer = {
                "load_id": load_row["load_id"],
                "origin_name": load_row["origin_name"],
                "dest_name": load_row["dest_name"],
                "weight_tons": float(load_row["weight_tons"] or 0.0),
                "pickup_lat": float(load_row["pickup_lat"] or 0.0),
                "pickup_lng": float(load_row["pickup_lng"] or 0.0),
                "dropoff_lat": float(load_row["dropoff_lat"] or 0.0),
                "dropoff_lng": float(load_row["dropoff_lng"] or 0.0),
                "pickup_latest_sec": float(load_row["pickup_latest_sec"] or 3600.0),
                "dropoff_latest_sec": float(load_row["dropoff_latest_sec"] or 7200.0),
                "insertion_mode": cand.get("insertion_mode"),
                "peak_onboard_load": cand.get("peak_onboard_load"),
                "marginal_cost": cand.get("marginal_cost"),
                "insert_pickup_index": cand.get("insert_pickup_index"),
                "insert_dropoff_index": cand.get("insert_dropoff_index"),
                "updated_stops": cand_stops,
                "route_coordinates": route_coords,
            }
            if manifest_stops:
                offer["manifest_stops"] = manifest_stops

            if cand.get("insertion_mode") == "MID_ROUTE_POOL":
                mid_route_pool_offers.append(offer)
            else:
                next_leg_backhaul_offers.append(offer)

    return {
        "vehicle_id": vehicle_id,
        "mid_route_pool_offers": mid_route_pool_offers,
        "next_leg_backhaul_offers": next_leg_backhaul_offers,
    }


# [FUSION-HOOK]: POST /api/v1/freight/loads/{load_id}/accept -> Accept and bind shipper load to carrier schedule with route polyline generation
@router.post("/loads/{load_id}/accept")
def accept_load(
    load_id: str,
    payload: Optional[AcceptLoadPayload] = None,
    vehicle_id: Optional[str] = Query(None),
) -> dict[str, Any]:
    actual_vehicle_id = (payload.vehicle_id if payload and payload.vehicle_id else vehicle_id)
    if not actual_vehicle_id:
        raise HTTPException(status_code=400, detail="vehicle_id is required")

    with _DB_LOCK:
        cur = _CONN.cursor()
        cur.execute("SELECT * FROM loads WHERE load_id = ?", (load_id,))
        load_row = cur.fetchone()
        if not load_row:
            raise HTTPException(status_code=404, detail=f"Load {load_id} not found")
        if load_row["status"] != "OPEN":
            raise HTTPException(status_code=409, detail=f"Load {load_id} is not OPEN (status: {load_row['status']})")

        cur.execute("SELECT * FROM carriers WHERE vehicle_id = ?", (actual_vehicle_id,))
        carrier_row = cur.fetchone()
        if not carrier_row:
            raise HTTPException(status_code=404, detail=f"Carrier {actual_vehicle_id} not found")

    carrier_stops = json.loads(carrier_row["active_stops_json"])
    carrier_vehicle = {
        "id": carrier_row["vehicle_id"],
        "capacity_max": float(carrier_row["capacity_max"]),
        "current_load": float(carrier_row["confirmed_onboard_load"]),
        "current_x": float(carrier_row["current_lat"]),
        "current_y": float(carrier_row["current_lng"]),
        "stops": carrier_stops,
    }

    manifest_stops = json.loads(load_row["manifest_stops_json"]) if ("manifest_stops_json" in load_row.keys() and load_row["manifest_stops_json"]) else None

    req = {
        "id": load_row["load_id"],
        "load": float(load_row["weight_tons"] or 0.0),
        "pickup": {
            "name": load_row["origin_name"] or "Origin",
            "x": float(load_row["pickup_lat"] or 0.0),
            "y": float(load_row["pickup_lng"] or 0.0),
            "latest_time_sec": float(load_row["pickup_latest_sec"] or 3600.0),
        },
        "dropoff": {
            "name": load_row["dest_name"] or "Dest",
            "x": float(load_row["dropoff_lat"] or 0.0),
            "y": float(load_row["dropoff_lng"] or 0.0),
            "latest_time_sec": float(load_row["dropoff_latest_sec"] or 7200.0),
        },
    }
    if manifest_stops:
        req["manifest_stops"] = manifest_stops

    dispatch_res = process_live_dispatch(
        fleet=[copy.deepcopy(carrier_vehicle)],
        request=req,
        platform="ENTROPIC_FREIGHT",
    )

    if dispatch_res.get("eligible_count", 0) == 0 or not dispatch_res.get("ranked_candidates"):
        raise HTTPException(status_code=409, detail=f"Load {load_id} is infeasible for vehicle {actual_vehicle_id}")

    cand = dispatch_res["ranked_candidates"][0]
    updated_stops = cand.get("updated_stops", [])
    route_coords = _compute_route_coordinates(carrier_vehicle["current_x"], carrier_vehicle["current_y"], updated_stops)

    with _DB_LOCK:
        cur = _CONN.cursor()
        cur.execute(
            "UPDATE loads SET status = 'ACCEPTED', assigned_vehicle_id = ? WHERE load_id = ?",
            (actual_vehicle_id, load_id),
        )
        cur.execute(
            "UPDATE carriers SET active_stops_json = ? WHERE vehicle_id = ?",
            (json.dumps(updated_stops), actual_vehicle_id),
        )
        _CONN.commit()

    return {
        "status": "ACCEPTED",
        "load_id": load_id,
        "vehicle_id": actual_vehicle_id,
        "insertion_mode": cand.get("insertion_mode"),
        "peak_onboard_load": cand.get("peak_onboard_load"),
        "active_stops": updated_stops,
        "route_coordinates": route_coords,
    }


# [FUSION-HOOK]: POST /api/v1/freight/carriers/{vehicle_id}/stops/{stop_index}/confirm -> One-tap stop progression updating onboard load, GPS, active stops, and route polyline
@router.post("/carriers/{vehicle_id}/stops/{stop_index}/confirm")
def confirm_carrier_stop(vehicle_id: str, stop_index: int) -> dict[str, Any]:
    with _DB_LOCK:
        cur = _CONN.cursor()
        cur.execute("SELECT * FROM carriers WHERE vehicle_id = ?", (vehicle_id,))
        carrier_row = cur.fetchone()
        if not carrier_row:
            raise HTTPException(status_code=404, detail=f"Carrier {vehicle_id} not found")

        active_stops = json.loads(carrier_row["active_stops_json"])
        if stop_index < 0 or stop_index >= len(active_stops):
            raise HTTPException(status_code=400, detail=f"Invalid stop_index {stop_index} (stops count: {len(active_stops)})")

        stop = active_stops.pop(stop_index)
        is_pickup = stop.get("is_pickup", True)
        load_id = stop.get("request_id")

        # Determine weight_tons for this stop
        weight_tons = 0.0
        if stop.get("weight_delta_tons") is not None:
            weight_tons = abs(float(stop["weight_delta_tons"]))
        elif stop.get("load_change") is not None and abs(float(stop["load_change"])) > 0:
            weight_tons = abs(float(stop["load_change"]))
        elif load_id:
            cur.execute("SELECT weight_tons FROM loads WHERE load_id = ?", (load_id,))
            l_row = cur.fetchone()
            if l_row and l_row["weight_tons"] is not None:
                weight_tons = float(l_row["weight_tons"])

        current_load = float(carrier_row["confirmed_onboard_load"])
        if is_pickup:
            new_load = round(current_load + weight_tons, 2)
            load_status = "PICKED_UP"
        else:
            new_load = round(max(0.0, current_load - weight_tons), 2)
            load_status = "DELIVERED"

        # Update carrier location to stop's location
        new_lat = float(stop.get("lat") if "lat" in stop else stop.get("x", carrier_row["current_lat"]))
        new_lng = float(stop.get("lng") if "lng" in stop else stop.get("y", carrier_row["current_lng"]))

        cur.execute(
            """
            UPDATE carriers
            SET confirmed_onboard_load = ?, active_stops_json = ?, current_lat = ?, current_lng = ?
            WHERE vehicle_id = ?
            """,
            (new_load, json.dumps(active_stops), new_lat, new_lng, vehicle_id),
        )

        if load_id:
            cur.execute("UPDATE loads SET status = ? WHERE load_id = ?", (load_status, load_id))

        _CONN.commit()

    route_coords = _compute_route_coordinates(new_lat, new_lng, active_stops)

    return {
        "status": "CONFIRMED",
        "vehicle_id": vehicle_id,
        "confirmed_stop": stop,
        "confirmed_onboard_load": new_load,
        "active_stops": active_stops,
        "route_coordinates": route_coords,
        "load_id": load_id,
        "load_status": load_status,
    }


# [FUSION-HOOK]: GET /api/v1/freight/carriers/{vehicle_id}/state -> Retrieve live carrier state including remaining capacity and full route polyline
@router.get("/carriers/{vehicle_id}/state")
def get_carrier_state(vehicle_id: str) -> dict[str, Any]:
    with _DB_LOCK:
        cur = _CONN.cursor()
        cur.execute("SELECT * FROM carriers WHERE vehicle_id = ?", (vehicle_id,))
        carrier_row = cur.fetchone()
        if not carrier_row:
            raise HTTPException(status_code=404, detail=f"Carrier {vehicle_id} not found")

    active_stops = json.loads(carrier_row["active_stops_json"])
    cap_max = float(carrier_row["capacity_max"])
    confirmed_load = float(carrier_row["confirmed_onboard_load"])
    remaining_cap = round(max(0.0, cap_max - confirmed_load), 2)
    cur_lat = float(carrier_row["current_lat"])
    cur_lng = float(carrier_row["current_lng"])

    route_coords = _compute_route_coordinates(cur_lat, cur_lng, active_stops)

    return {
        "vehicle_id": carrier_row["vehicle_id"],
        "capacity_max": cap_max,
        "confirmed_onboard_load": confirmed_load,
        "remaining_capacity_tons": remaining_cap,
        "current_lat": cur_lat,
        "current_lng": cur_lng,
        "active_stops": active_stops,
        "route_coordinates": route_coords,
    }
