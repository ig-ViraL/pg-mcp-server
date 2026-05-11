from src.db.manager import get_driver


async def handle_get_db_health() -> dict:
    driver = await get_driver()
    return await driver.get_db_health()
