import asyncio
import os
import json
import logging
from zsdis_client import ZSDISClient, _make_ssl_ctx

logging.basicConfig(level=logging.INFO)

async def test():
    config_path = "/Volumes/addons/zsdis_diportal_client/config.yaml"
    # we need the cookie string, maybe the user has it in options.json or config.yaml?
    # I'll just read it from the user's config file if I can, or use the zsdis_client class.
    # Actually, it's probably better to just make a raw aiohttp request with the cookie from config.yaml
    
    import yaml
    with open(config_path) as f:
        config = yaml.safe_load(f)
    
    cookie_string = config.get("options", {}).get("cookie_string", "")
    if not cookie_string:
        print("No cookie string found in config.yaml")
        return
        
    client = ZSDISClient(cookie_string=cookie_string)
    
    urls = [
        "https://www.diportal.sk/portal/api/delivery-points-list/getDeliveryPoints",
        "https://www.diportal.sk/portal/api/delivery-points-list/getDeliveryPointDetail",
        "https://www.diportal.sk/portal/api/commons/getBusinessPartnersForUser"
    ]
    
    import aiohttp
    async with aiohttp.ClientSession() as session:
        for url in urls:
            try:
                print(f"\\n--- Testing {url} ---")
                async with session.get(
                    url,
                    headers=client._base_headers(),
                    ssl=_make_ssl_ctx()
                ) as resp:
                    print(f"Status: {resp.status}")
                    text = await resp.text()
                    print(f"Response (first 500 chars): {text[:500]}")
            except Exception as e:
                print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test())
