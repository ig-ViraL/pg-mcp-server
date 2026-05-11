from src.db.manager import get_driver


async def handle_list_tables() -> dict:
    driver = await get_driver()
    return await driver.list_tables()
