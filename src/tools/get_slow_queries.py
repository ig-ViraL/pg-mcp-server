from src.db.manager import get_driver


async def handle_get_slow_queries(limit: int = 10) -> dict:
    driver = await get_driver()
    return await driver.get_slow_queries(limit)
