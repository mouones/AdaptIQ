"""Backward-compatible wrapper for the richer test-user seeder."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from setup_test_users import create_test_users


if __name__ == "__main__":
    import asyncio

    asyncio.run(create_test_users())
