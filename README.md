# Port Congestion Pulse

[![Pipeline and Pages](https://github.com/MonarchCastleTech/port-congestion-pulse/actions/workflows/pipeline.yml/badge.svg)](https://github.com/MonarchCastleTech/port-congestion-pulse/actions/workflows/pipeline.yml)

Autonomous 0–9 day early warning for global port-disruption pressure.

**Live dashboard:** https://monarchcastletech.github.io/port-congestion-pulse/

## Model

- 35% IMF PortWatch port-flow distortion
- 25% IMF PortWatch chokepoint-flow distortion
- 25% MET Norway/ECMWF seven-day port weather forecast
- 15% GDACS hazard proximity

No account, API key, or language model is required. Failed components are isolated; a validated component may be retained for at most 72 hours and is labelled retained. Available weights are renormalized.

## Reproduce

```bash
python -m pip install -r requirements.txt
python -m pytest -q
python pipeline/port_congestion_pulse_pipeline.py
python -m http.server 8000
```

Open `http://localhost:8000`. Full formulas, assumptions, thresholds, limitations, and primary-source links are published at `/methodology/`.

GitHub Actions tests the model, refreshes public data every six hours, commits the validated snapshot, and deploys GitHub Pages.
