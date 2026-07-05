"""
Initialize and test custom PostgreSQL database with pfe credentials
"""
import pytest
import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, text
import sys

pytestmark = pytest.mark.skip(reason="Standalone test script, not a pytest test")

async def create_custom_db():
    """Create custom PostgreSQL database with pfe credentials"""
    
    # First connect to default postgres database to create the new one
    default_url = "postgresql+asyncpg://pfe:fNvtHCN8bVWuFiDiG3ngJf1_xPLALLqU@localhost:5433/postgres"
    
    print("=" * 70)
    print("Setting Up Custom PostgreSQL Database")
    print("=" * 70)
    
    try:
        # Create engine for postgres default database
        engine = create_async_engine(default_url, echo=False, future=True, isolation_level='AUTOCOMMIT')
        print("\n✓ Connected to default postgres database")
        
        async with engine.begin() as conn:
            # Check if database exists
            check_result = await conn.execute(text(
                "SELECT 1 FROM pg_database WHERE datname='adaptiq_mw_db'"
            ))
            exists = check_result.fetchone()
            
            if not exists:
                # Create the new database
                await conn.execute(text("CREATE DATABASE adaptiq_mw_db"))
                print("✓ Database 'adaptiq_mw_db' created")
            else:
                print("✓ Database 'adaptiq_mw_db' already exists")
        
        await engine.dispose()
        
        # Now test the new database
        custom_url = "postgresql+asyncpg://pfe:fNvtHCN8bVWuFiDiG3ngJf1_xPLALLqU@localhost:5433/adaptiq_mw_db"
        
        print("\nTesting Custom Database Connection...")
        custom_engine = create_async_engine(custom_url, echo=False, future=True)
        
        async with custom_engine.begin() as conn:
            result = await conn.execute(text("SELECT 1"))
            await conn.commit()
        print("✓ Connection to adaptiq_mw_db successful")
        
        # Test basic operations
        async_session = sessionmaker(custom_engine, class_=AsyncSession, expire_on_commit=False)
        
        async with async_session() as session:
            # Create test table
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS test_credentials (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(255),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            await session.commit()
            print("✓ Test table created")
            
            # Insert test data
            await session.execute(text(
                "INSERT INTO test_credentials (username) VALUES (:username)"
            ), {"username": "pfe_test_user"})
            await session.commit()
            print("✓ Test data inserted")
            
            # Read test data
            result = await session.execute(text("SELECT * FROM test_credentials ORDER BY id DESC LIMIT 1"))
            row = result.fetchone()
            if row:
                print(f"✓ Test data retrieved: username={row[1]}")
            
            # Cleanup
            await session.execute(text("DROP TABLE IF EXISTS test_credentials"))
            await session.commit()
            print("✓ Test table cleaned up")
        
        await custom_engine.dispose()
        
        print("\n" + "=" * 70)
        print("✅ All tests passed!")
        print("=" * 70)
        print("\nCustom PostgreSQL Database Configuration Working:")
        print("  • Database created: adaptiq_mw_db ✓")
        print("  • User credentials: pfe (reference password) ✓")
        print("  • Port: 5433 ✓")
        print("  • Database connection: ✓")
        print("  • Table creation: ✓")
        print("  • Insert operation: ✓")
        print("  • Read operation: ✓")
        print("=" * 70)
        
    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        print(f"Error type: {type(e).__name__}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(create_custom_db())
