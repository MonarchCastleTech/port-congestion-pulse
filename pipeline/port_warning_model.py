#!/usr/bin/env python3
"""Autonomous port disruption early-warning model using public official feeds."""

from __future__ import annotations

import json
import math
import os
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "output.json"
USER_AGENT = (
    "MonarchCastleTech-PortCongestionPulse/2.0 "
    "(https://github.com/MonarchCastleTech/port-congestion-pulse; contact: ardakgul4@gmail.com)"
)
TIMEOUT = 35
FALLBACK_HOURS = 72

PORT_SERIES = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Ports_Data/FeatureServer/0"
CHOKE_SERIES = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Chokepoints_Data/FeatureServer/0"
PORTS = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/PortWatch_ports/FeatureServer/1"
CHOKEPOINTS = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/PortWatch_chokepoints_database/FeatureServer/0"
GDACS = "https://www.gdacs.org/contentdata/xml/gdacs_app_feed.json"
MET = "https://api.met.no/weatherapi/locationforecast/2.0/compact"

WEIGHTS = {
    "port_flow": 0.35,
    "chokepoint_flow": 0.25,
    "weather_forecast": 0.25,
    "hazard_proximity": 0.15,
}

SOURCES = [
    {
        "name": "IMF PortWatch — daily port activity",
        "url": "https://portwatch.imf.org/",
        "role": "AIS-derived port calls and shipment activity",
    },
    {
        "name": "IMF PortWatch — daily chokepoint activity",
        "url": "https://portwatch.imf.org/",
        "role": "AIS-derived traffic and carrying-capacity pressure",
    },
    {
        "name": "MET Norway Locationforecast",
        "url": "https://api.met.no/weatherapi/locationforecast/2.0/documentation",
        "role": "0–9 day ECMWF-based wind and precipitation forecast",
    },
    {
        "name": "GDACS",
        "url": "https://www.gdacs.org/feed_reference.aspx",
        "role": "Current global cyclone, flood, and earthquake proximity",
    },
]


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%m/%d/%Y %I:%M:%S %p", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def get_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = requests.get(
        url,
        params=params,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    return payload


def arcgis_query(
    layer: str,
    *,
    where: str = "1=1",
    fields: str = "*",
    order: str | None = None,
    count: int = 1000,
    geometry: bool = False,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "where": where,
        "outFields": fields,
        "returnGeometry": str(geometry).lower(),
        "outSR": 4326,
        "resultRecordCount": count,
        "f": "json",
    }
    if order:
        params["orderByFields"] = order
    payload = get_json(f"{layer}/query", params)
    return [feature.get("attributes", {}) for feature in payload.get("features", [])]


def safe_number(value: Any) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else 0.0
    except (TypeError, ValueError):
        return 0.0


def flow_pressure(recent: list[float], baseline: list[float]) -> tuple[float, float, str]:
    """Return pressure, percentage change, and direction for a two-sided anomaly."""
    recent_mean = mean(recent)
    baseline_mean = mean(baseline)
    if baseline_mean <= 0 or not recent:
        return 0.0, 0.0, "insufficient"
    change = (recent_mean / baseline_mean - 1.0) * 100.0
    pressure = clamp((abs(change) - 5.0) / 35.0 * 100.0)
    direction = "surge" if change >= 0 else "shortfall"
    return pressure, change, direction


def aggregate_entity_pressure(rows: list[dict[str, Any]]) -> float:
    if not rows:
        return 0.0
    weights = [max(1.0, safe_number(row.get("baseline_mean"))) for row in rows]
    weighted = sum(safe_number(row.get("pressure")) * weight for row, weight in zip(rows, weights)) / sum(weights)
    leaders = sorted((safe_number(row.get("pressure")) for row in rows), reverse=True)[:3]
    return round(clamp(weighted * 0.55 + mean(leaders) * 0.45), 1)


def directory(layer: str, limit: int) -> list[dict[str, Any]]:
    rows = arcgis_query(
        layer,
        fields="*",
        order="vessel_count_total DESC",
        count=limit,
    )
    for row in rows:
        if row.get("long") is None and row.get("lon") is not None:
            row["long"] = row["lon"]
    return [row for row in rows if row.get("portid")]


def collect_flow_component(
    directory_layer: str,
    series_layer: str,
    *,
    limit: int,
    value_field: str,
    key: str,
) -> dict[str, Any]:
    entities = directory(directory_layer, limit)
    evaluated: list[dict[str, Any]] = []
    latest_dates: list[str] = []
    for entity in entities:
        port_id = str(entity["portid"]).replace("'", "''")
        rows = arcgis_query(
            series_layer,
            where=f"portid='{port_id}'",
            fields=f"date,{value_field}",
            order="date DESC",
            count=42,
        )
        values = [safe_number(row.get(value_field)) for row in rows if row.get(value_field) is not None]
        if len(values) < 21:
            continue
        pressure, change, direction = flow_pressure(values[:7], values[7:35])
        if rows and rows[0].get("date"):
            latest_dates.append(str(rows[0]["date"]))
        evaluated.append(
            {
                "id": entity["portid"],
                "name": entity.get("portname") or entity["portid"],
                "country": entity.get("country"),
                "lat": round(safe_number(entity.get("lat")), 4),
                "lon": round(safe_number(entity.get("long")), 4),
                "recent_mean": round(mean(values[:7]), 1),
                "baseline_mean": round(mean(values[7:35]), 1),
                "change_pct": round(change, 1),
                "direction": direction,
                "pressure": round(pressure, 1),
                "observations": len(values),
            }
        )
    if not evaluated:
        raise RuntimeError(f"No valid {key} time series")
    evaluated.sort(key=lambda row: row["pressure"], reverse=True)
    score = aggregate_entity_pressure(evaluated)
    return {
        "key": key,
        "score": score,
        "status": band(score),
        "weight": WEIGHTS[key],
        "available": True,
        "retained": False,
        "latest_observation": max(latest_dates) if latest_dates else None,
        "coverage": len(evaluated),
        "method": "Seven-day mean versus preceding 28-day mean; two-sided 5–40% anomaly scale.",
        "evidence": evaluated[:10],
    }


def weather_site_score(max_wind: float, max_rain_24h: float) -> float:
    wind = clamp((max_wind - 12.0) / 14.0 * 100.0)
    rain = clamp((max_rain_24h - 30.0) / 90.0 * 100.0)
    return round(max(wind, rain), 1)


def parse_weather(payload: dict[str, Any]) -> tuple[float, float, str | None]:
    points = (payload.get("properties") or {}).get("timeseries") or []
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=7)
    winds: list[float] = []
    daily_rain: dict[str, float] = {}
    last_time: str | None = None
    for point in points:
        instant = parse_datetime(point.get("time"))
        if not instant or instant < now - timedelta(hours=3) or instant > horizon:
            continue
        details = (((point.get("data") or {}).get("instant") or {}).get("details") or {})
        winds.append(safe_number(details.get("wind_speed")))
        period = (point.get("data") or {}).get("next_1_hours") or (point.get("data") or {}).get("next_6_hours") or {}
        amount = safe_number((period.get("details") or {}).get("precipitation_amount"))
        daily_rain[instant.date().isoformat()] = daily_rain.get(instant.date().isoformat(), 0.0) + amount
        last_time = point.get("time")
    if not winds:
        raise RuntimeError("MET forecast contains no usable points")
    return max(winds), max(daily_rain.values(), default=0.0), last_time


def collect_weather_component(port_rows: list[dict[str, Any]]) -> dict[str, Any]:
    sites: list[dict[str, Any]] = []
    for port in port_rows[:10]:
        lat = round(safe_number(port.get("lat")), 4)
        lon = round(safe_number(port.get("long")), 4)
        payload = get_json(MET, {"lat": lat, "lon": lon})
        wind, rain, valid_to = parse_weather(payload)
        score = weather_site_score(wind, rain)
        sites.append(
            {
                "id": port.get("portid"),
                "name": port.get("portname"),
                "country": port.get("country"),
                "lat": lat,
                "lon": lon,
                "max_wind_ms": round(wind, 1),
                "max_precip_24h_mm": round(rain, 1),
                "pressure": score,
                "valid_to": valid_to,
            }
        )
    if not sites:
        raise RuntimeError("No MET port forecasts")
    sites.sort(key=lambda row: row["pressure"], reverse=True)
    score = round(clamp(mean([row["pressure"] for row in sites[:3]]) * 0.65 + mean([row["pressure"] for row in sites]) * 0.35), 1)
    return {
        "key": "weather_forecast",
        "score": score,
        "status": band(score),
        "weight": WEIGHTS["weather_forecast"],
        "available": True,
        "retained": False,
        "latest_observation": datetime.now(timezone.utc).date().isoformat(),
        "coverage": len(sites),
        "method": "Seven-day maximum wind and rolling daily precipitation at ten high-activity ports.",
        "evidence": sites,
    }


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def hazard_score(event_type: str, alert: str, severity: float, distance_km: float) -> float:
    base = {"green": 28.0, "orange": 65.0, "red": 95.0}.get(alert.lower(), 20.0)
    radius = {"TC": 900.0, "FL": 300.0, "EQ": 350.0}.get(event_type, 250.0)
    if distance_km >= radius:
        return 0.0
    proximity = 1.0 - distance_km / radius
    severity_bonus = 0.0
    if event_type == "TC":
        severity_bonus = clamp((severity - 90.0) / 120.0 * 35.0, 0.0, 35.0)
    elif event_type == "EQ":
        severity_bonus = clamp((severity - 5.0) / 2.5 * 35.0, 0.0, 35.0)
    return clamp((base + severity_bonus) * (0.35 + 0.65 * proximity))


def collect_hazard_component(port_rows: list[dict[str, Any]]) -> dict[str, Any]:
    payload = get_json(GDACS)
    events: list[dict[str, Any]] = []
    for feature in payload.get("features", []):
        properties = feature.get("properties") or {}
        event_type = str(properties.get("eventtype") or "")
        if event_type not in {"TC", "FL", "EQ"} or str(properties.get("iscurrent", "")).lower() != "true":
            continue
        geometry = feature.get("geometry") or {}
        coords = geometry.get("coordinates") or []
        if geometry.get("type") != "Point" or len(coords) < 2:
            continue
        event_lon, event_lat = safe_number(coords[0]), safe_number(coords[1])
        nearest = min(
            port_rows,
            key=lambda port: haversine(event_lat, event_lon, safe_number(port.get("lat")), safe_number(port.get("long"))),
        )
        distance = haversine(event_lat, event_lon, safe_number(nearest.get("lat")), safe_number(nearest.get("long")))
        severity_data = properties.get("severitydata") or {}
        severity = safe_number(severity_data.get("severity"))
        score = hazard_score(event_type, str(properties.get("alertlevel") or "Green"), severity, distance)
        if score <= 0:
            continue
        events.append(
            {
                "id": str(properties.get("eventid") or ""),
                "type": event_type,
                "name": properties.get("name") or properties.get("eventname"),
                "alert": properties.get("alertlevel") or "Green",
                "severity": severity_data.get("severitytext") or severity,
                "nearest_port": nearest.get("portname"),
                "country": nearest.get("country"),
                "distance_km": round(distance),
                "pressure": round(score, 1),
                "updated": properties.get("datemodified") or properties.get("todate"),
                "url": ((properties.get("url") or {}).get("report")),
            }
        )
    events.sort(key=lambda row: row["pressure"], reverse=True)
    score = round(clamp(mean([row["pressure"] for row in events[:3]])), 1) if events else 0.0
    return {
        "key": "hazard_proximity",
        "score": score,
        "status": band(score),
        "weight": WEIGHTS["hazard_proximity"],
        "available": True,
        "retained": False,
        "latest_observation": datetime.now(timezone.utc).date().isoformat(),
        "coverage": len(events),
        "method": "Current GDACS cyclone, flood, and earthquake distance to high-activity ports.",
        "evidence": events[:12],
    }


def band(score: float) -> str:
    if score < 25:
        return "BASELINE"
    if score < 45:
        return "WATCH"
    if score < 65:
        return "ELEVATED"
    if score < 80:
        return "HIGH"
    return "SEVERE"


def valid_previous(previous: dict[str, Any], now: datetime) -> bool:
    generated = parse_datetime((previous.get("meta") or {}).get("generated"))
    return bool(generated and timedelta(0) <= now - generated <= timedelta(hours=FALLBACK_HOURS))


def retained_component(previous: dict[str, Any], key: str, now: datetime, error: Exception) -> dict[str, Any] | None:
    if not valid_previous(previous, now):
        return None
    old = (previous.get("components") or {}).get(key)
    if not isinstance(old, dict) or not old.get("available"):
        return None
    retained = json.loads(json.dumps(old))
    retained["retained"] = True
    retained["retained_reason"] = f"Current fetch failed: {type(error).__name__}"
    return retained


def missing_component(key: str, error: Exception) -> dict[str, Any]:
    return {
        "key": key,
        "score": None,
        "status": "UNAVAILABLE",
        "weight": WEIGHTS[key],
        "available": False,
        "retained": False,
        "coverage": 0,
        "method": "Source unavailable; excluded and remaining weights renormalized.",
        "evidence": [],
        "error": type(error).__name__,
    }


def composite(components: dict[str, dict[str, Any]]) -> tuple[float, float, float]:
    available = [component for component in components.values() if component.get("available") and component.get("score") is not None]
    denominator = sum(safe_number(component.get("weight")) for component in available)
    if denominator <= 0:
        return 0.0, 0.0, 0.0
    raw = sum(safe_number(component["score"]) * safe_number(component["weight"]) for component in available) / denominator
    flow_elevated = any(
        safe_number((components.get(key) or {}).get("score")) >= 50
        for key in ("port_flow", "chokepoint_flow")
        if (components.get(key) or {}).get("available")
    )
    hazard_elevated = any(
        safe_number((components.get(key) or {}).get("score")) >= 40
        for key in ("weather_forecast", "hazard_proximity")
        if (components.get(key) or {}).get("available")
    )
    bonus = 5.0 if flow_elevated and hazard_elevated else 0.0
    return round(clamp(raw + bonus), 1), round(raw, 1), bonus


def load_previous() -> dict[str, Any]:
    try:
        return json.loads(OUTPUT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def update_history(previous: dict[str, Any], generated: str, score: float, status: str) -> list[dict[str, Any]]:
    history = [item for item in previous.get("history", []) if isinstance(item, dict) and item.get("generated")]
    history.append({"generated": generated, "score": score, "status": status})
    deduped: dict[str, dict[str, Any]] = {item["generated"]: item for item in history}
    return list(sorted(deduped.values(), key=lambda item: item["generated"]))[-60:]


def main() -> None:
    now = datetime.now(timezone.utc)
    generated = now.isoformat()
    previous = load_previous()
    notes: list[str] = []

    port_rows = directory(PORTS, 30)
    components: dict[str, dict[str, Any]] = {}
    collectors = {
        "port_flow": lambda: collect_flow_component(PORTS, PORT_SERIES, limit=12, value_field="portcalls", key="port_flow"),
        "chokepoint_flow": lambda: collect_flow_component(CHOKEPOINTS, CHOKE_SERIES, limit=8, value_field="n_total", key="chokepoint_flow"),
        "weather_forecast": lambda: collect_weather_component(port_rows),
        "hazard_proximity": lambda: collect_hazard_component(port_rows),
    }
    for key, collector in collectors.items():
        try:
            components[key] = collector()
            print(f"[live] {key}: {components[key]['score']}")
        except Exception as error:  # feed isolation is intentional
            retained = retained_component(previous, key, now, error)
            components[key] = retained or missing_component(key, error)
            note = f"{key}: {'retained validated snapshot' if retained else 'unavailable'} ({type(error).__name__})"
            notes.append(note)
            print(f"[fallback] {note}")

    score, raw_score, bonus = composite(components)
    coverage = sum(1 for component in components.values() if component.get("available"))
    retained_count = sum(1 for component in components.values() if component.get("retained"))
    confidence = "HIGH" if coverage == 4 and retained_count == 0 else "MEDIUM" if coverage >= 3 else "LOW"
    mode = "live" if coverage == 4 and retained_count == 0 else "partial" if coverage else "unavailable"
    status = band(score)

    output = {
        "meta": {
            "project": "port-congestion-pulse",
            "generated": generated,
            "mode": mode,
            "version": "2.0.0",
            "horizon": "0–9 days",
            "classification": "port-disruption-pressure-not-measured-vessel-waiting-time-or-closure-probability",
            "coverage": f"{coverage}/4",
            "confidence": confidence,
            "source_notes": notes,
        },
        "warning": {
            "score": score,
            "raw_score": raw_score,
            "concurrence_bonus": bonus,
            "status": status,
            "headline": f"Global port disruption pressure is {status.lower()} at {score:.1f}/100.",
            "interpretation": "The index combines abnormal vessel-flow patterns with independent forward weather and hazard exposure. It is an early-warning pressure measure, not observed vessel waiting time.",
        },
        "components": components,
        "history": update_history(previous, generated, score, status),
        "sources": SOURCES,
        "methodology": {
            "weights": WEIGHTS,
            "fallback_hours": FALLBACK_HOURS,
            "activity_window": "7-day mean versus preceding 28-day mean",
            "forecast_window": "7 days within a published 0–9 day product horizon",
            "concurrence_rule": "+5 only when an AIS flow component is ≥50 and an independent forecast/hazard component is ≥40",
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"score={score} status={status} coverage={coverage}/4 confidence={confidence}")


if __name__ == "__main__":
    main()
