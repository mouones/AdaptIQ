import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main
import httpx

async def run():
    async with httpx.AsyncClient(app=main.app, base_url='http://test') as c:
        r = await c.post('/api/auth/signup', json={"username":"asgiuser","email":"asgi@example.com","password":"Secret123"})
        print('status', r.status_code)
        print('body', r.text)

if __name__ == '__main__':
    asyncio.run(run())
