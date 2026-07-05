"""In-memory operational monitoring for request, queue, and generation telemetry."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
import math
from typing import Optional


class Monitoring:
    """In-memory monitoring service for tracking API and queue metrics."""

    def __init__(self) -> None:
        self.request_stats = {
            "total_requests": 0,
            "total_errors": 0,
            "total_rate_limits": 0,
            "total_duration_ms": 0.0,
        }
        self.recent_errors = deque(maxlen=100)
        self.recent_rate_limits = deque(maxlen=50)
        self.recent_requests = deque(maxlen=200)
        self.request_counts = defaultdict(int)
        self.endpoint_stats = defaultdict(
            lambda: {
                "requests": 0,
                "errors": 0,
                "latencies": deque(maxlen=500),
                "methods": defaultdict(int),
                "status_codes": defaultdict(int),
                "last_seen": None,
            }
        )
        self.queue_depths: dict[str, dict] = {}
        self.room_delivery_stats = defaultdict(
            lambda: {
                "bank_hits": 0,
                "queue_hits": 0,
                "offline_hits": 0,
                "other_hits": 0,
            }
        )
        self.generation_stats = defaultdict(
            lambda: {
                "attempts": 0,
                "accepted": 0,
                "rejected": 0,
                "total_generation_ms": 0.0,
                "provider_429_count": 0,
                "provider_statuses": defaultdict(int),
            }
        )
        self.provider_statuses = defaultdict(lambda: defaultdict(int))

    def record_request(
        self,
        endpoint: str,
        method: str = "GET",
        status_code: int = 200,
        duration_ms: float = 0.0,
    ) -> None:
        """Record a request, including method/status/latency telemetry."""
        safe_duration = max(0.0, float(duration_ms or 0.0))
        self.request_stats["total_requests"] += 1
        self.request_stats["total_duration_ms"] += safe_duration
        self.request_counts[endpoint] += 1
        endpoint_stat = self.endpoint_stats[endpoint]
        endpoint_stat["requests"] += 1
        endpoint_stat["methods"][method] += 1
        endpoint_stat["status_codes"][str(int(status_code))] += 1
        endpoint_stat["latencies"].append(safe_duration)
        endpoint_stat["last_seen"] = datetime.now(timezone.utc).isoformat()
        if int(status_code) >= 400:
            endpoint_stat["errors"] += 1

        self.recent_requests.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "endpoint": endpoint,
                "method": method,
                "status_code": int(status_code),
                "duration_ms": round(safe_duration, 2),
            }
        )

    def record_error(
        self,
        endpoint: str,
        method: str,
        status_code: int,
        error_type: str,
        error_message: str,
        duration_ms: float,
    ) -> None:
        """Record an error."""
        self.request_stats["total_errors"] += 1
        self.recent_errors.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "endpoint": endpoint,
                "method": method,
                "status_code": status_code,
                "error_type": error_type,
                "error_message": error_message,
                "duration_ms": duration_ms,
            }
        )

    def record_rate_limit(self, client_ip: str, endpoint: str, method: str) -> None:
        """Record a rate limit hit."""
        self.request_stats["total_rate_limits"] += 1
        self.recent_rate_limits.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "client_ip": client_ip,
                "endpoint": endpoint,
                "method": method,
            }
        )

    def record_queue_depth(self, room: str, queue_key: str, depth: int) -> None:
        """Store the latest observed Redis ready-queue depth."""
        self.queue_depths[str(queue_key)] = {
            "room": str(room or "unknown"),
            "depth": max(0, int(depth or 0)),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def record_question_source(self, room: str, source: str) -> None:
        """Track where served room questions came from."""
        room_key = str(room or "unknown").strip().lower() or "unknown"
        source_key = str(source or "other").strip().lower()
        bucket = self.room_delivery_stats[room_key]
        if source_key == "bank":
            bucket["bank_hits"] += 1
        elif source_key == "queue":
            bucket["queue_hits"] += 1
        elif source_key == "offline":
            bucket["offline_hits"] += 1
        else:
            bucket["other_hits"] += 1

    def record_provider_status(self, provider: str, status_code: int) -> None:
        """Track upstream provider responses such as 429 throttling."""
        provider_key = str(provider or "unknown").strip().lower() or "unknown"
        status_key = str(int(status_code or 0))
        self.provider_statuses[provider_key][status_key] += 1

    def record_generation_attempt(
        self,
        room: str,
        *,
        provider: str,
        provider_status: int,
        generation_ms: float,
        accepted: bool,
    ) -> None:
        """Record one background generation attempt."""
        room_key = str(room or "unknown").strip().lower() or "unknown"
        stats = self.generation_stats[room_key]
        stats["attempts"] += 1
        stats["total_generation_ms"] += max(0.0, float(generation_ms or 0.0))
        if accepted:
            stats["accepted"] += 1
        else:
            stats["rejected"] += 1
        status_value = int(provider_status or 0)
        stats["provider_statuses"][str(status_value)] += 1
        if status_value == 429:
            stats["provider_429_count"] += 1
        self.record_provider_status(provider, status_value)

    def get_stats(self) -> dict:
        """Get current monitoring statistics."""
        total_requests = int(self.request_stats.get("total_requests", 0))
        total_duration_ms = float(self.request_stats.get("total_duration_ms", 0.0))
        avg_latency_ms = (total_duration_ms / total_requests) if total_requests > 0 else 0.0

        endpoint_telemetry: dict[str, dict] = {}
        for endpoint, entry in self.endpoint_stats.items():
            latencies = list(entry["latencies"])
            latency_avg = (sum(latencies) / len(latencies)) if latencies else 0.0
            latency_p95 = 0.0
            if latencies:
                ordered = sorted(latencies)
                idx = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1))
                latency_p95 = ordered[idx]

            req_count = int(entry["requests"])
            err_count = int(entry["errors"])
            endpoint_telemetry[endpoint] = {
                "requests": req_count,
                "errors": err_count,
                "error_rate": round((err_count / req_count) if req_count else 0.0, 4),
                "avg_latency_ms": round(latency_avg, 2),
                "p95_latency_ms": round(float(latency_p95), 2),
                "methods": dict(entry["methods"]),
                "status_codes": dict(entry["status_codes"]),
                "last_seen": entry["last_seen"],
            }

        question_delivery: dict[str, dict] = {}
        for room, entry in self.room_delivery_stats.items():
            bank_hits = int(entry["bank_hits"])
            queue_hits = int(entry["queue_hits"])
            offline_hits = int(entry["offline_hits"])
            other_hits = int(entry["other_hits"])
            total_serves = bank_hits + queue_hits + offline_hits + other_hits
            question_delivery[room] = {
                "bank_hits": bank_hits,
                "queue_hits": queue_hits,
                "offline_hits": offline_hits,
                "other_hits": other_hits,
                "total_serves": total_serves,
                "bank_hit_ratio": round((bank_hits / total_serves) if total_serves else 0.0, 4),
                "queue_hit_ratio": round((queue_hits / total_serves) if total_serves else 0.0, 4),
                "offline_hit_ratio": round((offline_hits / total_serves) if total_serves else 0.0, 4),
            }

        queues_by_room: dict[str, dict] = {}
        for queue_key, entry in self.queue_depths.items():
            room = str(entry.get("room") or "unknown")
            depth = int(entry.get("depth") or 0)
            room_entry = queues_by_room.setdefault(
                room,
                {"queue_count": 0, "total_depth": 0, "max_depth": 0},
            )
            room_entry["queue_count"] += 1
            room_entry["total_depth"] += depth
            room_entry["max_depth"] = max(int(room_entry["max_depth"]), depth)

        generation_metrics: dict[str, dict] = {}
        for room, entry in self.generation_stats.items():
            attempts = int(entry["attempts"])
            total_generation_ms = float(entry["total_generation_ms"])
            generation_metrics[room] = {
                "attempts": attempts,
                "accepted": int(entry["accepted"]),
                "rejected": int(entry["rejected"]),
                "avg_generation_ms": round((total_generation_ms / attempts) if attempts else 0.0, 2),
                "provider_429_count": int(entry["provider_429_count"]),
                "provider_statuses": dict(entry["provider_statuses"]),
            }

        return {
            **self.request_stats,
            "endpoints": dict(self.request_counts),
            "average_latency_ms": round(avg_latency_ms, 2),
            "endpoint_telemetry": endpoint_telemetry,
            "question_delivery": question_delivery,
            "question_queues": {
                "depth_by_queue": dict(self.queue_depths),
                "depth_by_room": queues_by_room,
            },
            "generation_metrics": generation_metrics,
            "provider_statuses": {
                provider: dict(statuses)
                for provider, statuses in self.provider_statuses.items()
            },
            "recent_requests_count": len(self.recent_requests),
            "recent_errors_count": len(self.recent_errors),
            "recent_rate_limits_count": len(self.recent_rate_limits),
            "recent_requests": list(self.recent_requests)[-30:],
            "recent_errors": list(self.recent_errors)[-30:],
            "recent_rate_limits": list(self.recent_rate_limits)[-30:],
        }


_monitoring: Optional[Monitoring] = None


def get_monitoring() -> Monitoring:
    """Get or create the monitoring singleton."""
    global _monitoring
    if _monitoring is None:
        _monitoring = Monitoring()
    return _monitoring


def reset_monitoring() -> None:
    """Reset monitoring state for tests."""
    global _monitoring
    _monitoring = Monitoring()
