from src.db.manager import get_driver


async def handle_run_query(sql: str, limit: int = 100) -> dict:
    driver = await get_driver()
    return await driver.run_query(sql, limit)
