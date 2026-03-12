"""Datadog tools for the Triage Agent — search logs, list monitors, get error details."""

import time

from datadog_api_client import Configuration, ApiClient
from datadog_api_client.v2.api.logs_api import LogsApi
from datadog_api_client.v2.model.logs_list_request import LogsListRequest
from datadog_api_client.v2.model.logs_query_filter import LogsQueryFilter
from datadog_api_client.v2.model.logs_sort import LogsSort
from datadog_api_client.v1.api.monitors_api import MonitorsApi as MonitorsApiV1
from langchain_core.tools import tool

from src.config import settings

# Simple rate-limit: track last call time and enforce minimum gap
_last_api_call = 0.0
_MIN_GAP_SECONDS = 4.0  # 3 req / 10s limit → 4s gap is safe


def _rate_limit_wait():
    """Wait if needed to respect Datadog's rate limit."""
    global _last_api_call
    now = time.time()
    elapsed = now - _last_api_call
    if elapsed < _MIN_GAP_SECONDS:
        time.sleep(_MIN_GAP_SECONDS - elapsed)
    _last_api_call = time.time()


def _get_dd_config() -> Configuration:
    config = Configuration()
    config.api_key["apiKeyAuth"] = settings.dd_api_key
    config.api_key["appKeyAuth"] = settings.dd_app_key
    # Strip protocol if present — API client expects just hostname
    site = settings.dd_site.replace("https://", "").replace("http://", "").rstrip("/")
    config.server_variables["site"] = site
    return config


@tool
def search_logs(query: str, cluster: str, timeframe: str = "1h", limit: int = 20) -> str:
    """Search application logs in Datadog for errors, exceptions, or any log pattern.

    Args:
        query: Datadog log query string (e.g. 'status:error service:auth-api').
        cluster: Kubernetes cluster or environment tag to scope the search (e.g. 'prod-us-east-1', 'staging-eu').
        timeframe: How far back to search — e.g. '1h', '6h', '1d'. Default '1h'.
        limit: Max number of log entries to return. Default 20.
    """
    config = _get_dd_config()

    # Scope query to the specified cluster
    scoped_query = f"kube_cluster_name:{cluster} {query}"

    # Convert shorthand to relative time format
    time_map = {"1h": "now-1h", "6h": "now-6h", "1d": "now-1d", "7d": "now-7d"}
    from_time = time_map.get(timeframe, f"now-{timeframe}")

    body = LogsListRequest(
        filter=LogsQueryFilter(
            query=scoped_query,
            _from=from_time,
            to="now",
        ),
        sort=LogsSort.TIMESTAMP_DESCENDING,
        page={"limit": min(limit, 50)},
    )

    _rate_limit_wait()
    with ApiClient(config) as api_client:
        api = LogsApi(api_client)
        response = api.list_logs(body=body)

    if not response.data:
        return f"No logs found for query '{query}' in the last {timeframe}."

    lines = [f"Found {len(response.data)} log(s) for query '{query}' (last {timeframe}):\n"]
    for i, log_entry in enumerate(response.data, 1):
        attrs = log_entry.attributes
        msg = getattr(attrs, "message", "(no message)")
        svc = getattr(attrs, "service", "unknown")
        status = getattr(attrs, "status", "unknown")
        ts = getattr(attrs, "timestamp", "")
        lines.append(f"  {i}. [{status}] {svc} @ {ts}\n     {msg[:300]}")

    return "\n".join(lines)


@tool
def list_triggered_monitors(cluster: str) -> str:
    """List currently triggered (alerting) Datadog monitors for a specific cluster.

    Args:
        cluster: Kubernetes cluster or environment tag to filter monitors (e.g. 'prod-us-east-1').

    Returns a summary of monitors in Alert or Warn state for the given cluster.
    """
    config = _get_dd_config()

    _rate_limit_wait()
    with ApiClient(config) as api_client:
        api = MonitorsApiV1(api_client)
        monitors = api.list_monitors(monitor_tags=f"kube_cluster_name:{cluster}")

    triggered = [m for m in monitors if m.overall_state in ("Alert", "Warn", "No Data")]

    if not triggered:
        return "No monitors are currently in Alert or Warn state."

    lines = [f"Found {len(triggered)} triggered monitor(s):\n"]
    for m in triggered:
        lines.append(
            f"  - [{m.overall_state}] {m.name} (ID: {m.id})\n"
            f"    Type: {m.type}\n"
            f"    Message: {(m.message or '(none)')[:200]}\n"
            f"    Tags: {', '.join(m.tags) if m.tags else '(none)'}"
        )

    return "\n".join(lines)


@tool
def get_monitor_details(monitor_id: int) -> str:
    """Get detailed information about a specific Datadog monitor.

    Args:
        monitor_id: The numeric ID of the Datadog monitor.
    """
    config = _get_dd_config()

    _rate_limit_wait()
    with ApiClient(config) as api_client:
        api = MonitorsApiV1(api_client)
        m = api.get_monitor(monitor_id)

    return (
        f"Monitor #{m.id}: {m.name}\n"
        f"State: {m.overall_state}\n"
        f"Type: {m.type}\n"
        f"Query: {m.query}\n"
        f"Message: {m.message or '(none)'}\n"
        f"Tags: {', '.join(m.tags) if m.tags else '(none)'}\n"
        f"Created: {m.created}\n"
        f"Modified: {m.modified}"
    )


# Collect all Datadog tools
datadog_tools = [
    search_logs,
    list_triggered_monitors,
    get_monitor_details,
]
