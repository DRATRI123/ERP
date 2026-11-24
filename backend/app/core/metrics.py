"""Minimal Prometheus metrics (text exposition format 0.0.4), kept in process.

Run one API process per container (scale with more containers) or put each behind its own
scrape target; counters are per process, which is how Prometheus expects them.
"""

from __future__ import annotations

import threading
from collections import defaultdict

BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class _Http:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.count: dict[tuple[str, str, str], int] = defaultdict(int)
        self.hist: dict[tuple[str, str], list[float]] = {}

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        cls = f"{status // 100}xx"
        with self.lock:
            self.count[(method, route, cls)] += 1
            h = self.hist.setdefault((method, route), [0.0] * (len(BUCKETS) + 2))
            for i, b in enumerate(BUCKETS):
                if seconds <= b:
                    h[i] += 1
            h[-2] += 1  # +Inf / count
            h[-1] += seconds  # sum

    def reset(self) -> None:
        with self.lock:
            self.count.clear()
            self.hist.clear()


http = _Http()


def _esc(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _labels(**kv: str) -> str:
    return "{" + ",".join(f'{k}="{_esc(str(v))}"' for k, v in kv.items()) + "}"


def render(gauges: list[tuple[str, str, dict[str, str], float]]) -> str:
    """``gauges``: (name, help, labels, value) computed at scrape time."""
    out = ["# HELP erp_http_requests_total HTTP requests by route template and status class",
           "# TYPE erp_http_requests_total counter"]
    with http.lock:
        for (m, r, c), n in sorted(http.count.items()):
            out.append(f"erp_http_requests_total{_labels(method=m, route=r, status=c)} {n}")
        out += ["# HELP erp_http_request_duration_seconds Request latency",
                "# TYPE erp_http_request_duration_seconds histogram"]
        for (m, r), h in sorted(http.hist.items()):
            for i, b in enumerate(BUCKETS):
                out.append(f"erp_http_request_duration_seconds_bucket{_labels(method=m, route=r, le=str(b))} {int(h[i])}")
            out.append(f"erp_http_request_duration_seconds_bucket{_labels(method=m, route=r, le='+Inf')} {int(h[-2])}")
            out.append(f"erp_http_request_duration_seconds_count{_labels(method=m, route=r)} {int(h[-2])}")
            out.append(f"erp_http_request_duration_seconds_sum{_labels(method=m, route=r)} {h[-1]:.6f}")
    seen: set[str] = set()
    for name, help_, labels, value in gauges:
        if name not in seen:
            out += [f"# HELP {name} {help_}", f"# TYPE {name} gauge"]
            seen.add(name)
        out.append(f"{name}{_labels(**labels) if labels else ''} {value:g}")
    return "\n".join(out) + "\n"
