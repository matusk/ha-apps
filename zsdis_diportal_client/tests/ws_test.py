import asyncio
import json
import os
import aiohttp

async def test_ws():
    token = os.environ.get('SUPERVISOR_TOKEN')
    if not token:
        # Since we run outside of bashio sometimes, we need a real token or run via bashio
        print("No token, try running via docker exec")
        return

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect('ws://supervisor/core/api/websocket') as ws:
            msg = await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": token})
            msg = await ws.receive_json()
            if msg.get('type') != 'auth_ok':
                print("Auth failed", msg)
                return

            await ws.send_json({"id": 1, "type": "config/schedule/list"})
            msg = await ws.receive_json()
            print("List schedules:", json.dumps(msg, indent=2))

asyncio.run(test_ws())
