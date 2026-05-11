from src.db.manager import get_driver


async def handle_explain_query(sql: str, analyze: bool = False) -> dict:
    driver = await get_driver()
    return await driver.explain_query(sql, analyze)
