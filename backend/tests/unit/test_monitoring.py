"""Regression tests for test monitoring behavior."""

import pytest
from services.monitoring import Monitoring, get_monitoring, reset_monitoring

def test_monitoring_request_stats():
    reset_monitoring()
    mon = get_monitoring()
    
    mon.record_request("/api/test1")
    mon.record_request("/api/test1")
    mon.record_request("/api/test2")
    
    stats = mon.get_stats()
    assert stats["total_requests"] == 3
    assert stats["endpoints"]["/api/test1"] == 2
    assert stats["endpoints"]["/api/test2"] == 1

def test_monitoring_error_recording():
    mon = Monitoring()
    mon.record_error(
        endpoint="/api/fail",
        method="GET",
        status_code=500,
        error_type="ValueError",
        error_message="Oops",
        duration_ms=150.5
    )
    
    stats = mon.get_stats()
    assert stats["total_errors"] == 1
    assert stats["recent_errors_count"] == 1
    
    err = mon.recent_errors[0]
    assert err["endpoint"] == "/api/fail"
    assert err["status_code"] == 500
    assert err["error_message"] == "Oops"

def test_monitoring_rate_limit_recording():
    mon = Monitoring()
    mon.record_rate_limit(client_ip="127.0.0.1", endpoint="/api/fast", method="POST")
    
    stats = mon.get_stats()
    assert stats["total_rate_limits"] == 1
    assert stats["recent_rate_limits_count"] == 1
    
    rl = mon.recent_rate_limits[0]
    assert rl["client_ip"] == "127.0.0.1"

def test_monitoring_bounded_queues():
    mon = Monitoring()
    
    # Exceed error queue maxlen (100)
    for i in range(110):
        mon.record_error("/err", "GET", 500, "Err", str(i), 10.0)
        
    # Exceed rate limit queue maxlen (50)
    for i in range(60):
        mon.record_rate_limit(f"10.0.0.{i}", "/rl", "GET")
        
    stats = mon.get_stats()
    assert stats["total_errors"] == 110
    assert stats["recent_errors_count"] == 100  # Bounded
    
    assert stats["total_rate_limits"] == 60
    assert stats["recent_rate_limits_count"] == 50  # Bounded
    
    # Oldest should be dropped, youngest kept (i=109 for errors)
    assert mon.recent_errors[-1]["error_message"] == "109"
