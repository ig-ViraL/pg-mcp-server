from src.db.manager import get_driver


async def handle_search_schema(query: str) -> dict:
    driver = await get_driver()
    return await driver.search_schema(query)
