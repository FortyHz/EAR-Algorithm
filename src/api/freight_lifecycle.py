import copy
import json
import sqlite3
import threading
from typing import Any, List, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

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
                origin_name TEXT NOT NULL,
                dest_name TEXT NOT NULL,
                pickup_lat REAL NOT NULL,
                pickup_lng REAL NOT NULL,
                dropoff_lat REAL NOT NULL,
                dropoff_lng REAL NOT NULL,
                weight_tons REAL NOT NULL,
                pickup_latest_sec REAL NOT NULL,
                dropoff_latest_sec REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN',
                assigned_vehicle_id TEXT
            )
        """)
        _CONN.commit()


_init_db()


class RegisterCarrierPayload(BaseModel):
    vehicle_id: str
    capacity_max: float
    current_lat: float
    current_lng: float
    confirmed_onboard_load: float = 0.0
    active_stops: list[dict[str, Any]] = Field(default_factory=list)


class CreateLoadPayload(BaseModel):
    load_id: str
    origin_name: str
    dest_name: str
    pickup_lat: float
    pickup_lng: float
    dropoff_lat: float
    dropoff_lng: float
    weight_tons: float
    pickup_latest_sec: float
    dropoff_latest_sec: float
    status: str = "OPEN"


class AcceptLoadPayload(BaseModel):
    vehicle_id: Optional[str] = None


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


@router.post("/loads")
def create_load(payload: CreateLoadPayload) -> dict[str, Any]:
    with _DB_LOCK:
        cur = _CONN.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO loads (
                load_id, origin_name, dest_name, pickup_lat, pickup_lng, dropoff_lat, dropoff_lng,
                weight_tons, pickup_latest_sec, dropoff_latest_sec, status, assigned_vehicle_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.load_id,
                payload.origin_name,
                payload.dest_name,
                float(payload.pickup_lat),
                float(payload.pickup_lng),
                float(payload.dropoff_lat),
                float(payload.dropoff_lng),
                float(payload.weight_tons),
                float(payload.pickup_latest_sec),
                float(payload.dropoff_latest_sec),
                payload.status,
                None,
            ),
        )
        _CONN.commit()

    load_dict = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    return {
        "status": "CREATED",
        "load": load_dict,
    }


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
        req = {
            "id": load_row["load_id"],
            "load": float(load_row["weight_tons"]),
            "pickup": {
                "name": load_row["origin_name"],
                "x": float(load_row["pickup_lat"]),
                "y": float(load_row["pickup_lng"]),
                "latest_time_sec": float(load_row["pickup_latest_sec"]),
            },
            "dropoff": {
                "name": load_row["dest_name"],
                "x": float(load_row["dropoff_lat"]),
                "y": float(load_row["dropoff_lng"]),
                "latest_time_sec": float(load_row["dropoff_latest_sec"]),
            },
        }

        dispatch_res = process_live_dispatch(
            fleet=[copy.deepcopy(carrier_vehicle)],
            request=req,
            platform="ENTROPIC_FREIGHT",
        )

        if dispatch_res.get("eligible_count", 0) > 0 and dispatch_res.get("ranked_candidates"):
            cand = dispatch_res["ranked_candidates"][0]
            offer = {
                "load_id": load_row["load_id"],
                "origin_name": load_row["origin_name"],
                "dest_name": load_row["dest_name"],
                "weight_tons": float(load_row["weight_tons"]),
                "pickup_lat": float(load_row["pickup_lat"]),
                "pickup_lng": float(load_row["pickup_lng"]),
                "dropoff_lat": float(load_row["dropoff_lat"]),
                "dropoff_lng": float(load_row["dropoff_lng"]),
                "pickup_latest_sec": float(load_row["pickup_latest_sec"]),
                "dropoff_latest_sec": float(load_row["dropoff_latest_sec"]),
                "insertion_mode": cand.get("insertion_mode"),
                "peak_onboard_load": cand.get("peak_onboard_load"),
                "marginal_cost": cand.get("marginal_cost"),
                "insert_pickup_index": cand.get("insert_pickup_index"),
                "insert_dropoff_index": cand.get("insert_dropoff_index"),
                "updated_stops": cand.get("updated_stops", []),
            }
            if cand.get("insertion_mode") == "MID_ROUTE_POOL":
                mid_route_pool_offers.append(offer)
            else:
                next_leg_backhaul_offers.append(offer)

    return {
        "vehicle_id": vehicle_id,
        "mid_route_pool_offers": mid_route_pool_offers,
        "next_leg_backhaul_offers": next_leg_backhaul_offers,
    }


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

    req = {
        "id": load_row["load_id"],
        "load": float(load_row["weight_tons"]),
        "pickup": {
            "name": load_row["origin_name"],
            "x": float(load_row["pickup_lat"]),
            "y": float(load_row["pickup_lng"]),
            "latest_time_sec": float(load_row["pickup_latest_sec"]),
        },
        "dropoff": {
            "name": load_row["dest_name"],
            "x": float(load_row["dropoff_lat"]),
            "y": float(load_row["dropoff_lng"]),
            "latest_time_sec": float(load_row["dropoff_latest_sec"]),
        },
    }

    dispatch_res = process_live_dispatch(
        fleet=[copy.deepcopy(carrier_vehicle)],
        request=req,
        platform="ENTROPIC_FREIGHT",
    )

    if dispatch_res.get("eligible_count", 0) == 0 or not dispatch_res.get("ranked_candidates"):
        raise HTTPException(status_code=409, detail=f"Load {load_id} is infeasible for vehicle {actual_vehicle_id}")

    cand = dispatch_res["ranked_candidates"][0]
    updated_stops = cand.get("updated_stops", [])

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
    }


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

        # Determine weight_tons
        weight_tons = 0.0
        if load_id:
            cur.execute("SELECT weight_tons FROM loads WHERE load_id = ?", (load_id,))
            l_row = cur.fetchone()
            if l_row:
                weight_tons = float(l_row["weight_tons"])

        if weight_tons == 0.0:
            weight_tons = abs(float(stop.get("load_change", 0.0)))

        current_load = float(carrier_row["confirmed_onboard_load"])
        if is_pickup:
            new_load = round(current_load + weight_tons, 2)
            load_status = "PICKED_UP"
        else:
            new_load = round(max(0.0, current_load - weight_tons), 2)
            load_status = "DELIVERED"

        # Update carrier location to stop's location
        new_lat = float(stop.get("x", carrier_row["current_lat"]))
        new_lng = float(stop.get("y", carrier_row["current_lng"]))

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

    return {
        "status": "CONFIRMED",
        "vehicle_id": vehicle_id,
        "confirmed_stop": stop,
        "confirmed_onboard_load": new_load,
        "active_stops": active_stops,
        "load_id": load_id,
        "load_status": load_status,
    }
