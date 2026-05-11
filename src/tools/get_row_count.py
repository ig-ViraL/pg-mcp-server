from src.db.manager import get_driver


async def handle_get_row_count(table_name: str) -> dict:
    driver = await get_driver()
    return await driver.get_row_count(table_name)
