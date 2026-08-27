from datetime import datetime, timedelta, timezone

from pipeline.port_warning_model import (
    band,
    composite,
    flow_pressure,
    hazard_score,
    retained_component,
    valid_previous,
    weather_site_score,
)


def component(key, score, weight, available=True):
    return {"key": key, "score": score, "weight": weight, "available": available, "evidence": []}


def test_flow_pressure_is_two_sided_and_bounded():
    surge, surge_change, surge_direction = flow_pressure([150] * 7, [100] * 28)
    shortfall, shortfall_change, shortfall_direction = flow_pressure([50] * 7, [100] * 28)
    assert surge == 100
    assert shortfall == 100
    assert surge_change == 50
    assert shortfall_change == -50
    assert surge_direction == "surge"
    assert shortfall_direction == "shortfall"


def test_small_flow_change_is_baseline():
    score, change, _ = flow_pressure([103] * 7, [100] * 28)
    assert score == 0
    assert round(change) == 3


def test_weather_thresholds():
    assert weather_site_score(10, 5) == 0
    assert weather_site_score(26, 5) == 100
    assert weather_site_score(10, 120) == 100


def test_hazard_distance_decay_and_bounds():
    near = hazard_score("TC", "Red", 180, 20)
    far = hazard_score("TC", "Red", 180, 800)
    assert 0 <= far < near <= 100
    assert hazard_score("TC", "Red", 180, 901) == 0


def test_composite_renormalizes_missing_weight():
    components = {
        "port_flow": component("port_flow", 60, 0.35),
        "chokepoint_flow": component("chokepoint_flow", None, 0.25, False),
        "weather_forecast": component("weather_forecast", 20, 0.25),
        "hazard_proximity": component("hazard_proximity", 20, 0.15),
    }
    score, raw, bonus = composite(components)
    assert raw == 38.7
    assert score == raw
    assert bonus == 0


def test_concurrence_requires_independent_domains():
    components = {
        "port_flow": component("port_flow", 60, 0.35),
        "chokepoint_flow": component("chokepoint_flow", 10, 0.25),
        "weather_forecast": component("weather_forecast", 50, 0.25),
        "hazard_proximity": component("hazard_proximity", 10, 0.15),
    }
    score, raw, bonus = composite(components)
    assert bonus == 5
    assert score == raw + 5


def test_fallback_expires_after_72_hours():
    now = datetime.now(timezone.utc)
    recent = {"meta": {"generated": (now - timedelta(hours=71)).isoformat()}}
    stale = {"meta": {"generated": (now - timedelta(hours=73)).isoformat()}}
    assert valid_previous(recent, now)
    assert not valid_previous(stale, now)


def test_retained_component_is_marked_and_not_mutated():
    now = datetime.now(timezone.utc)
    previous = {
        "meta": {"generated": (now - timedelta(hours=2)).isoformat()},
        "components": {"port_flow": component("port_flow", 45, 0.35)},
    }
    retained = retained_component(previous, "port_flow", now, RuntimeError("offline"))
    assert retained and retained["retained"] is True
    assert previous["components"]["port_flow"].get("retained") is None


def test_band_boundaries():
    assert [band(value) for value in (0, 24.9, 25, 45, 65, 80, 100)] == [
        "BASELINE", "BASELINE", "WATCH", "ELEVATED", "HIGH", "SEVERE", "SEVERE"
    ]
