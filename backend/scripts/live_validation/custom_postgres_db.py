"""
Test script for PostgreSQL database with pfe credentials and custom database name
"""
import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, text
import sys

async def test_custom_postgres_db():
    """Test connection to custom PostgreSQL database with reference credentials"""
    
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://pfe:change_this_password@localhost:5433/adaptiq_mw_db",
    )
    
    print("=" * 70)
    print("Testing Custom PostgreSQL Database Configuration")
    print("=" * 70)
    print(f"\nDatabase URL (masked): postgresql+asyncpg://pfe:***@localhost:5433/adaptiq_mw_db")
    print(f"Database Name: adaptiq_mw_db")
    print(f"User: pfe")
    print(f"Port: 5433")
    
    try:
        # Create async engine
        engine = create_async_engine(
            database_url,
            echo=False,
            future=True
        )
        print("\nâœ“ Engine created successfully")
        
        # Test connection
        async with engine.begin() as conn:
            result = await conn.execute(text("SELECT 1"))
            await conn.commit()
        print("âœ“ Database connection successful")
        
        # Test basic operations
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        
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
            print("âœ“ Test table created")
            
            # Insert test data
            await session.execute(text(
                "INSERT INTO test_credentials (username) VALUES (:username)"
            ), {"username": "pfe_test_user"})
            await session.commit()
            print("âœ“ Test data inserted")
            
            # Read test data
            result = await session.execute(text("SELECT * FROM test_credentials ORDER BY id DESC LIMIT 1"))
            row = result.fetchone()
            if row:
                print(f"âœ“ Test data retrieved: username={row[1]}")
            
            # Cleanup
            await session.execute(text("DROP TABLE IF EXISTS test_credentials"))
            await session.commit()
            print("âœ“ Test table cleaned up")
        
        await engine.dispose()
        
        print("\n" + "=" * 70)
        print("âœ… All tests passed!")
        print("=" * 70)
        print("\nCustom PostgreSQL Database Configuration Working:")
        print("  â€¢ Database connection: âœ“")
        print("  â€¢ Table creation: âœ“")
        print("  â€¢ Insert operation: âœ“")
        print("  â€¢ Read operation: âœ“")
        print("  â€¢ Credentials: pfe user with reference password âœ“")
        print("=" * 70)
        
    except Exception as e:
        print(f"\nâŒ Error: {str(e)}")
        print(f"Error type: {type(e).__name__}")
        await engine.dispose()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(test_custom_postgres_db())

