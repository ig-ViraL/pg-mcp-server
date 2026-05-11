from src.db.manager import get_driver


async def handle_describe_table(table_name: str) -> dict:
    driver = await get_driver()
    return await driver.describe_table(table_name)
