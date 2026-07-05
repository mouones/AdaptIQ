"""Validate the alternate SQLite test database configuration.

Run from the backend folder:
    python scripts/live_validation/alt_db.py
"""

import asyncio
import sys
from pathlib import Path
from dotenv import load_dotenv
import os

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Load backend/.env.test instead of backend/.env.
env_test = BACKEND_ROOT / ".env.test"
load_dotenv(env_test)

import uuid
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from database.models import Base, User

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
ENVIRONMENT = os.getenv("ENVIRONMENT", "testing")
AUTO_CREATE_TABLES = os.getenv("AUTO_CREATE_TABLES", "true").lower() == "true"

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

async def test_alternative_db():
    """Test SQLite alternative database configuration"""
    print(f"\n{'='*70}")
    print(f"  AdaptIQ â€” Alternative Database Test (.env.test)")
    print(f"{'='*70}\n")
    
    print(f"Configuration:")
    print(f"  DATABASE_URL: {DATABASE_URL}")
    print(f"  ENVIRONMENT: {ENVIRONMENT}")
    print(f"  AUTO_CREATE_TABLES: {AUTO_CREATE_TABLES}\n")
    
    try:
        # Create async engine
        engine = create_async_engine(
            DATABASE_URL,
            echo=False,
        )
        
        SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
        
        print(f"{GREEN}âœ“{RESET} Engine created")
        
        # Create tables
        if AUTO_CREATE_TABLES:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            print(f"{GREEN}âœ“{RESET} Tables created (AUTO_CREATE_TABLES=true)")
        
        # Test CRUD: Create user
        test_user_id = str(uuid.uuid4())
        test_email = "test_alt_db@adaptiq.test"
        test_username = "test_alt_db_user"
        
        async with SessionFactory() as session:
            user = User(
                id=uuid.UUID(test_user_id),
                email=test_email,
                username=test_username,
                password_hash="hashed_password_test",
                points=0,
                level="Novice",
                is_active=True,
            )
            session.add(user)
            await session.commit()
        
        print(f"{GREEN}âœ“{RESET} User created: {test_email}")
        
        # Test CRUD: Read user
        async with SessionFactory() as session:
            from sqlalchemy import select
            stmt = select(User).where(User.email == test_email)
            result = await session.execute(stmt)
            fetched_user = result.scalar_one_or_none()
            
            if fetched_user:
                print(f"{GREEN}âœ“{RESET} User retrieved: {fetched_user.username} (ID: {str(fetched_user.id)[:8]}...)")
            else:
                raise ValueError("User not found after insert")
        
        # Summary
        print(f"\n{GREEN}All tests passed!{RESET}")
        print(f"\nSQLite Alternative Database Configuration Working:")
        print(f"  â€¢ Database connection: {GREEN}âœ“{RESET}")
        print(f"  â€¢ Table creation: {GREEN}âœ“{RESET}")
        print(f"  â€¢ Insert operation: {GREEN}âœ“{RESET}")
        print(f"  â€¢ Read operation: {GREEN}âœ“{RESET}\n")
        
        # Suggest production setup
        print(f"{YELLOW}For production with PostgreSQL:${RESET}")
        print(f"  Update .env with reference credentials:")
        print(f"    DATABASE_URL=postgresql+asyncpg://pfe:change_this_postgres_password@localhost:5432/adaptive_learning\n")
        
        await engine.dispose()
        return True
        
    except Exception as e:
        print(f"\n{RED}âœ— Test failed:{RESET}")
        print(f"  {e}\n")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = asyncio.run(test_alternative_db())
    sys.exit(0 if success else 1)


