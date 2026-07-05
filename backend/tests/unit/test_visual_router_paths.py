"""Regression tests for test visual router paths behavior."""

from __future__ import annotations

from routers.visual_room import visual_router


def test_visual_router_exposes_expected_endpoints() -> None:
    routes: set[tuple[str, str]] = set()

    for route in getattr(visual_router, "routes", []):
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if not path:
            continue

        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            routes.add((method, path))

    expected = {
        ("POST", "/api/visual/start-session"),
        ("GET", "/api/visual/next"),
        ("POST", "/api/visual/submit"),
        ("GET", "/api/visual/hint"),
        ("GET", "/api/visual/explanation"),
        ("POST", "/api/visual/session/{session_id}/end"),
    }

    missing = expected - routes
    assert not missing, f"Missing visual endpoints: {sorted(missing)}"
