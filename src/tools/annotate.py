from src.db.manager import get_driver


async def handle_annotate_table(table_name: str, comment: str) -> dict:
    driver = await get_driver()
    return await driver.annotate_table(table_name, comment)


async def handle_annotate_column(table_name: str, column_name: str, comment: str) -> dict:
    driver = await get_driver()
    return await driver.annotate_column(table_name, column_name, comment)
