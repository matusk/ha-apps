import asyncio
import logging
from hdo_parser import HDOParser

logging.basicConfig(level=logging.DEBUG)

async def test():
    parser = HDOParser(primary_code="259", water_heater_code="246")
    html = await parser._fetch_schedule_page()
    if not html:
        print("Failed to fetch HTML")
        return
    
    print(f"Fetched HTML, length: {len(html)}")
    
    intervals_primary = parser._parse_schedule(html, "259")
    print("Primary:", intervals_primary)
    
    intervals_wh = parser._parse_schedule(html, "246")
    print("WH:", intervals_wh)

if __name__ == "__main__":
    asyncio.run(test())
