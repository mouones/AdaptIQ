import httpx

url = 'http://127.0.0.1:8000/api/auth/signup'
payload = {"username":"pytestuser","email":"pytest@example.com","password":"Secret123"}
with httpx.Client() as c:
    r = c.post(url, json=payload)
    print(r.status_code)
    print(r.text)
