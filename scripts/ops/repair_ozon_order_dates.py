import argparse
import asyncio
import json

from app.infrastructure.async_database import AsyncDatabase
from app.infrastructure.async_orders_repository import AsyncOrdersRepository


async def run(*, apply: bool) -> None:
    database = AsyncDatabase()
    try:
        orders = AsyncOrdersRepository(database)
        for store in await orders.stores_with_orders():
            result = await orders.repair_processing_dates(store, apply=apply)
            print(json.dumps({"store": store, **result.model_dump()}), flush=True)
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Align stored Ozon orders with their processing timestamps.")
    parser.add_argument("--apply", action="store_true", help="Save changes; by default, only count them.")
    asyncio.run(run(apply=parser.parse_args().apply))


if __name__ == "__main__":
    main()
