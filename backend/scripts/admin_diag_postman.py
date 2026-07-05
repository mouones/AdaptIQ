"""Maintenance helper for admin diag postman workflows."""

import requests

BASE = "http://127.0.0.1:8000"


def login(email: str, password: str):
    response = requests.post(
        f"{BASE}/api/auth/login",
        json={"email": email, "password": password},
        timeout=20,
    )
    token = None
    user = None
    if response.ok:
        body = response.json()
        token = body.get("access_token")
        user = body.get("user", {})
    return response.status_code, response.text[:400], token, user


admin_status, admin_body, admin_token, admin_user = login(
    "admin.master@example.com", "AdminPass123!"
)
user_status, user_body, user_token, user_user = login(
    "challenge.e@example.com", "TestPass123!"
)

print("login admin:", admin_status, (admin_user or {}).get("is_admin"))
print("login user :", user_status, (user_user or {}).get("is_admin"))
if admin_status != 200:
    print("admin login body:", admin_body)
if user_status != 200:
    print("user login body:", user_body)

paths = [
    ("/api/admin/overview", "GET", None),
    ("/api/admin/top-concepts?limit=5", "GET", None),
    ("/api/admin/users?page=1&per_page=10", "GET", None),
    ("/api/admin/users/{user_id}", "GET", "user1"),
    ("/api/admin/users/{user_id}?is_active=false", "PATCH", "user2"),
    ("/api/admin/users/{user_id}?is_active=true", "PATCH", "user2"),
    ("/api/admin/questions?page=1&per_page=10", "GET", None),
    ("/api/admin/sessions?page=1&per_page=10", "GET", None),
    ("/api/admin/monitoring", "GET", None),
]

uid1 = (admin_user or {}).get("id", "")
uid2 = (user_user or {}).get("id", "")

print("\n---- non-admin token checks ----")
for path, method, who in paths:
    resolved = path
    if "{user_id}" in resolved:
        resolved = resolved.replace("{user_id}", uid1 if who == "user1" else uid2)
    headers = {"Authorization": f"Bearer {user_token}"} if user_token else {}
    response = requests.request(method, f"{BASE}{resolved}", headers=headers, timeout=20)
    print(method, resolved, "=>", response.status_code)

print("\n---- admin token checks ----")
for path, method, who in paths:
    resolved = path
    if "{user_id}" in resolved:
        resolved = resolved.replace("{user_id}", uid1 if who == "user1" else uid2)
    headers = {"Authorization": f"Bearer {admin_token}"} if admin_token else {}
    response = requests.request(method, f"{BASE}{resolved}", headers=headers, timeout=20)
    preview = response.text[:220].replace("\n", " ")
    print(method, resolved, "=>", response.status_code, "|", preview)
