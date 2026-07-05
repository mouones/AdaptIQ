import httpx
import time

url = 'http://127.0.0.1:8000/api/auth/signup'
payload = {"username":"tailuser","email":"tail@example.com","password":"Secret123"}
with httpx.Client() as c:
    r = c.post(url, json=payload)
    print('RESPONSE', r.status_code, r.text)
    time.sleep(0.5)

print('\n--- LOG TAIL ---')
with open('logs/backend.log', 'rb') as f:
    f.seek(0,2)
    size = f.tell()
    f.seek(max(0,size-4000))
    print(f.read().decode('utf-8','ignore'))
