"""Authenticated API flow integration script.

Run manually against a live backend on localhost:8000.
"""
from __future__ import annotations

import asyncio
import sys
import uuid

import httpx
BACKEND_URL = "http://localhost:8000"


async def test_authenticated_flow() -> bool:
    """Exercise auth, ownership, and representative protected endpoints."""

    print("\n" + "=" * 70)
    print("Authenticated API Flow Integration Test")
    print("=" * 70)
    print(f"\nBackend URL: {BACKEND_URL}")

    try:
        async with httpx.AsyncClient(base_url=BACKEND_URL) as client:
            run_id = uuid.uuid4().hex[:8]
            test_email = f"flowtest-{run_id}@example.com"
            test_username = f"flowtest_{run_id}"

            print("\n[Test 0] Backend Health Check")
            try:
                response = await client.get("/health")
                print(f"  Status: {response.status_code}")
                if response.status_code == 200:
                    health = response.json()
                    print("  [OK] Backend is healthy")
                    print(f"    Status: {health.get('status')}")
                else:
                    print(f"  [WARN] Health check returned {response.status_code}")
            except Exception as exc:
                print(f"  [FAIL] Cannot connect to backend: {exc}")
                print(f"     Make sure backend is running on {BACKEND_URL}")
                return False

            print("\n[Test 1] User Signup")
            signup_data = {
                "email": test_email,
                "password": "SecureTestPass123!",
                "username": test_username,
            }
            response = await client.post("/api/auth/signup", json=signup_data)
            print(f"  Status: {response.status_code}")

            if response.status_code not in {200, 201}:
                print(f"  Response: {response.text[:200]}")
                print("  [FAIL] Signup failed")
                return False

            signup_result = response.json()
            user_id = signup_result.get("user", {}).get("id")
            print(f"  [OK] User created: {str(user_id)[:8]}")

            print("\n[Test 2] User Login")
            login_data = {
                "email": test_email,
                "password": "SecureTestPass123!",
            }
            response = await client.post("/api/auth/login", json=login_data)
            print(f"  Status: {response.status_code}")

            if response.status_code != 200:
                print(f"  Response: {response.text[:200]}")
                print("  [FAIL] Login failed")
                return False

            login_result = response.json()
            access_token = login_result.get("access_token")
            if not access_token:
                print("  [FAIL] Access token missing")
                return False
            print("  [OK] Access token received")

            print("\n[Test 3] Access Protected Endpoint (Valid Token, Same User)")
            headers = {"Authorization": f"Bearer {access_token}"}
            response = await client.get(f"/api/challenge/user/{user_id}/rank", headers=headers)
            print(f"  Status: {response.status_code}")

            if response.status_code == 200:
                print("  [OK] Protected endpoint accessible")
                result = response.json()
                print(f"    Response keys: {list(result.keys())}")
            else:
                print(f"  Response: {response.text[:200]}")
                print(f"  [FAIL] Protected endpoint returned {response.status_code}")
                return False

            print("\n[Test 4] Ownership Check (Different User ID - Should Fail)")
            fake_user_id = "00000000-0000-0000-0000-000000000000"
            response = await client.get(f"/api/challenge/user/{fake_user_id}/rank", headers=headers)
            print(f"  Status: {response.status_code}")

            if response.status_code == 403:
                print("  [OK] Ownership check working - 403 Forbidden")
                error_result = response.json()
                print(f"    Response: {error_result.get('detail', 'Access denied')}")
            else:
                print(f"  [FAIL] Expected 403, got {response.status_code}")
                print(f"  Response: {response.text[:200]}")
                return False

            print("\n[Test 5] Unauthenticated Access (Should Fail)")
            async with httpx.AsyncClient(base_url=BACKEND_URL) as anonymous_client:
                response = await anonymous_client.get(f"/api/challenge/user/{user_id}/rank")
            print(f"  Status: {response.status_code}")

            if response.status_code == 401:
                print("  [OK] Authentication required - 401 Unauthorized")
            else:
                print(f"  Response: {response.text[:200]}")
                print(f"  [FAIL] Expected 401, got {response.status_code}")
                return False

            print("\n[Test 6] Custom Room Protected Endpoint")
            response = await client.get("/api/custom/topics", headers=headers)
            print(f"  Status: {response.status_code}")

            if response.status_code in {200, 400}:
                print("  [OK] Custom room endpoint accessible")
            else:
                print(f"  Response: {response.text[:100]}")
                print(f"  [FAIL] Unexpected custom room status {response.status_code}")
                return False

            print("\n[Test 7] Onboarding Protected Endpoint")
            response = await client.get(f"/api/onboarding/status?user_id={user_id}", headers=headers)
            print(f"  Status: {response.status_code}")

            if response.status_code in {200, 400}:
                print("  [OK] Onboarding endpoint accessible")
            else:
                print(f"  Response: {response.text[:100]}")
                print(f"  [FAIL] Unexpected onboarding status {response.status_code}")
                return False

        print("\n" + "=" * 70)
        print("[OK] Authenticated Flow Test Completed Successfully")
        print("=" * 70)
        print("\nKey Validations Passed:")
        print("  - Backend health check")
        print("  - User signup")
        print("  - User login and token generation")
        print("  - Protected endpoint access with valid token")
        print("  - Ownership validation")
        print("  - Unauthenticated access rejection")
        print("  - Multiple protected router endpoints")
        print("=" * 70)
        return True

    except Exception as exc:
        print(f"\n[FAIL] Test Error: {exc}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    result = asyncio.run(test_authenticated_flow())
    sys.exit(0 if result else 1)

