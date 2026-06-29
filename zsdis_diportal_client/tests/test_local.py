#!/usr/bin/env python3
"""
ZSDIS Diportal Client — Local Test Runner
=========================================
Run this OUTSIDE of Home Assistant to verify:
 1. Cookies load correctly
 2. Session is valid (diportal.sk accepts them)
 3. HDO schedule fetches from public ZSDIS page
 4. Data endpoint can be discovered / data fetched
 5. Response parsing works

Usage:
 pip install aiohttp beautifulsoup4
 python3 test_local.py

Optional flags:
 python3 test_local.py --debug     # verbose HTTP logging
 python3 test_local.py --date 2026-06-24 # test a specific date
 python3 test_local.py --discover    # intercept XHR via Playwright to find API endpoint
"""

import asyncio
import json
import logging
import os
import sys
import argparse
from datetime import date, timedelta
from pathlib import Path

# ── Override addon internal paths to work locally ────────────────────────────
# Redirect /data/* → ./test_data/ and /config/* → /Volumes/config/
TEST_DATA_DIR = Path(__file__).parent / 'test_data'
TEST_DATA_DIR.mkdir(exist_ok=True)

# Monkey-patch the path constants BEFORE importing the modules
import importlib
import types

# We'll patch after import via environment
os.environ['TEST_MODE'] = '1'

# ── Patch paths in zsdis_client before import ────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

import zsdis_client as _zc
_zc.SESSION_FILE = TEST_DATA_DIR / 'session.json'
_zc.COOKIES_BOOTSTRAP_FILE = Path('/Volumes/config/zsdis_cookies.json')
_zc.API_CONFIG_FILE = TEST_DATA_DIR / 'api_config.json'

from zsdis_client import ZSDISClient
from hdo_parser import HDOParser

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
  level=logging.DEBUG,
  format='%(asctime)s %(levelname)-8s %(name)-20s %(message)s',
  datefmt='%H:%M:%S',
)
# Silence noisy libraries
logging.getLogger('asyncio').setLevel(logging.WARNING)
logging.getLogger('aiohttp').setLevel(logging.WARNING)
logging.getLogger('charset_normalizer').setLevel(logging.WARNING)
logger = logging.getLogger('test_local')


# ── Test helpers ──────────────────────────────────────────────────────────────

def print_section(title: str):
  print(f'\n{"="*60}')
  print(f' {title}')
  print('='*60)


def print_result(ok: bool, message: str):
  icon = '' if ok else ''
  print(f' {icon} {message}')


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_cookie_loading(client: ZSDISClient) -> bool:
  print_section('1. Cookie Loading')
  
  # Try bootstrap file
  ok = client._load_bootstrap_cookies()
  print_result(ok, f'Bootstrap cookies: {len(client.cookies)} cookies loaded')
  
  if ok:
    important = ['JSESSIONID', 'JSESSIONMARKID', 'MYSAPSSO2']
    for name in important:
      present = name in client.cookies
      val_preview = client.cookies.get(name, '')[:20] + '...' if name in client.cookies else 'MISSING'
      print_result(present, f' {name}: {val_preview}')
  
  return ok


async def test_session_validity(client: ZSDISClient) -> bool:
  print_section('2. Session Validity (HTTP request to diportal.sk)')
  print(' Making request to https://www.diportal.sk/portal/group/abonent ...')
  
  valid = await client.is_session_valid()
  print_result(valid, f'Session {"is VALID ✔" if valid else "is EXPIRED — need new cookies"}')
  
  if not valid:
    print('\n  Session is expired. Steps to get fresh cookies:')
    print('   1. Open Chrome → go to https://www.diportal.sk')
    print('   2. Log in manually')
    print('   3. Install "Get cookies.txt LOCALLY" extension')
    print('   4. Click extension → select JSON format → Save')
    print('   5. Copy file to /Volumes/config/zsdis_cookies.json')
    print('   6. Re-run this script')
  
  return valid


async def test_raw_portal_pages(client: ZSDISClient):
  """Make raw HTTP requests and dump responses to understand portal structure"""
  print_section('3. Portal Structure Discovery (raw requests)')
  
  import aiohttp
  import ssl
  
  ssl_ctx = ssl.create_default_context()
  ssl_ctx.check_hostname = False
  ssl_ctx.verify_mode = ssl.CERT_NONE

  test_urls = [
    'https://www.diportal.sk/portal/group/abonent',
    'https://www.diportal.sk/portal/group/abonent/abonentske-miesta',
    'https://www.diportal.sk/portal/',
  ]

  cookie_header = client._build_cookie_header()

  async with aiohttp.ClientSession() as session:
    for url in test_urls:
      try:
        async with session.get(
          url,
          headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Cookie': cookie_header,
            'Accept': 'text/html,application/xhtml+xml,application/json,*/*',
          },
          ssl=ssl_ctx,
          allow_redirects=True,
          timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
          body = await resp.text(errors='replace')
          is_login = 'logon' in resp.url.path.lower() or 'login' in resp.url.path.lower()
          has_content = len(body) > 500

          status_icon = '' if resp.status == 200 and not is_login else ''
          print(f'\n {status_icon} GET {url}')
          print(f'   → {resp.url} [HTTP {resp.status}]')
          print(f'   Content-Type: {resp.content_type}')
          print(f'   Body length: {len(body)} chars')
          
          if is_login:
            print('   Redirected to login page — session expired!')
          elif has_content:
            # Save full response for inspection
            out_file = TEST_DATA_DIR / f'response_{url.split("/")[-1] or "root"}.html'
            out_file.write_text(body)
            print(f'   Full response saved to: {out_file}')
            # Show snippet
            snippet = body[:300].replace('\n', ' ').strip()
            print(f'   Preview: {snippet}...')

      except Exception as e:
        print(f'\n GET {url}: {e}')


async def test_playwright_discovery(client: ZSDISClient, username: str, password: str):
  """Use Playwright to log in + navigate + capture XHR calls to find the data endpoint"""
  print_section('4. Playwright API Endpoint Discovery')
  
  try:
    from playwright.async_api import async_playwright
  except ImportError:
    print('  Playwright not installed. Install with:')
    print('    pip install playwright && playwright install chromium')
    return

  print(' 🎭 Launching headless Chromium...')
  
  discovered_requests = []

  async with async_playwright() as p:
    browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
    context = await browser.new_context(ignore_https_errors=True)

    # If we have cookies, inject them
    if client.cookies:
      pw_cookies = [
        {'name': k, 'value': v, 'domain': 'www.diportal.sk', 'path': '/'}
        for k, v in client.cookies.items()
      ]
      await context.add_cookies(pw_cookies)
      print(f' Injected {len(pw_cookies)} cookies into Playwright context')

    # Intercept ALL requests
    async def on_request(request):
      url = request.url
      method = request.method
      if method in ('POST', 'GET') and 'diportal' in url:
        if any(kw in url.lower() for kw in ['data', 'profile', 'interval', 'meran', 'odber', 'consumption']):
          discovered_requests.append({'url': url, 'method': method, 'post_data': request.post_data})
          print(f' 🔍 XHR: {method} {url}')

    context.on('request', on_request)
    page = await context.new_page()

    # Navigate to portal
    print(' Navigating to portal...')
    await page.goto('https://www.diportal.sk/portal/', wait_until='networkidle', timeout=30000)
    print(f' Current URL: {page.url}')

    # If not logged in, try login
    if 'logon' in page.url.lower() or 'login' in page.url.lower():
      if username and password:
        print(' Attempting login with provided credentials...')
        for sel in ['input[type="text"]', '#logonuidfield', 'input[name*="user"]']:
          try:
            await page.fill(sel, username, timeout=2000)
            break
          except Exception:
            pass
        for sel in ['input[type="password"]', '#logonpassfield']:
          try:
            await page.fill(sel, password, timeout=2000)
            break
          except Exception:
            pass
        await page.click('button[type="submit"], input[type="submit"], .urBtnStd', timeout=5000)
        await page.wait_for_load_state('networkidle', timeout=20000)
        print(f' After login URL: {page.url}')
      else:
        print('  Not logged in and no credentials provided')
        print('   Pass --username / --password to attempt login')

    # Try navigating to measured data section
    print(' Navigating to measured data (Int. dáta)...')
    try:
      await page.goto(
        'https://www.diportal.sk/portal/group/abonent/abonentske-miesta',
        wait_until='networkidle', timeout=20000
      )
      await asyncio.sleep(3)
      print(f' URL: {page.url}')
      
      # Take screenshot for inspection
      screenshot = TEST_DATA_DIR / 'portal_screenshot.png'
      await page.screenshot(path=str(screenshot), full_page=True)
      print(f' 📸 Screenshot saved: {screenshot}')
    except Exception as e:
      print(f' Navigation error: {e}')

    # Extract updated cookies
    pw_cookies = await context.cookies()
    if pw_cookies:
      client.cookies = {c['name']: c['value'] for c in pw_cookies}
      client._save_session()
      print(f' Updated session saved ({len(pw_cookies)} cookies)')

    await browser.close()

  if discovered_requests:
    print(f'\n 🎯 Found {len(discovered_requests)} data-related XHR requests:')
    for req in discovered_requests:
      print(f'   {req["method"]} {req["url"]}')
      if req.get('post_data'):
        print(f'   POST data: {req["post_data"][:200]}')
    
    # Save discovered endpoints
    discovery_file = TEST_DATA_DIR / 'discovered_endpoints.json'
    discovery_file.write_text(json.dumps(discovered_requests, indent=2))
    print(f'\n All discovered endpoints saved to: {discovery_file}')
  else:
    print('\n ℹ️ No data XHR requests captured.')
    print(' You may need to manually click "Int. dáta" in the portal.')
    print(' Check the screenshot to see current state.')


async def test_data_fetch(client: ZSDISClient, target_date: date):
  print_section(f'5. Data Fetch for {target_date}')

  data = await client.fetch_day(target_date)

  if data:
    total = data.get('total_kwh', 0)
    vt = data.get('vt_kwh', 0)
    nt = data.get('nt_kwh', 0)

    print_result(total > 0, f'Total: {total:.3f} kWh')
    print(f'    VT (high): {vt:.3f} kWh')
    print(f'    NT (low):  {nt:.3f} kWh')

    if total == 0:
      print('\n  Got a response but total is 0.')
      print('  Check /data/raw/raw_response_*.json to inspect the API response.')
      print('  The parser in zsdis_client.py may need adjusting for the actual response format.')
  else:
    print_result(False, 'No data returned — check logs above for HTTP errors')

async def test_hdo(hdo_primary: str, hdo_water_heater: str):
  print_section(f'6. HDO Status ({hdo_primary}, {hdo_water_heater})')
  
  parser = HDOParser(primary_code=hdo_primary, water_heater_code=hdo_water_heater)
  
  try:
    status = await parser.get_current_status()
    print_result(True, f'Current tariff ({hdo_primary}): {status["primary_tariff"].upper()}')
    print(f'    Next switch: {status["next_switch"]}')
    print(f'    Water heater ({hdo_water_heater}): {"ON" if status["water_heater_on"] else "OFF"}')
    print(f'    Schedule source: {status.get("schedule_source", "unknown")}')
  except Exception as e:
    print_result(False, f'HDO error: {e}')



# ── Main ──────────────────────────────────────────────────────────────────────

async def run_tests(args):
  username = args.username or os.environ.get('ZSDIS_USERNAME', '')
  password = args.password or os.environ.get('ZSDIS_PASSWORD', '')

  if args.debug:
    logging.getLogger().setLevel(logging.DEBUG)
  else:
    logging.getLogger().setLevel(logging.INFO)

  client = ZSDISClient(username=username, password=password)

  print('\nZSDIS Diportal Client — Local Test')
  print(f'  Test data dir: {TEST_DATA_DIR}')
  print(f'  Cookie file:  {_zc.COOKIES_BOOTSTRAP_FILE}')
  print(f'  Session file: {_zc.SESSION_FILE}')

  # 1. Load cookies
  cookies_ok = await test_cookie_loading(client)
  if not cookies_ok:
    print('\nCannot proceed without cookies. Place cookies JSON at:')
    print(f'  {_zc.COOKIES_BOOTSTRAP_FILE}')
    return

  # 2. Validate session
  session_ok = await test_session_validity(client)

  # 3. Raw portal pages (always useful)
  await test_raw_portal_pages(client)

  # 4. Playwright discovery (if requested or session invalid)
  if args.discover or not session_ok:
    await test_playwright_discovery(client, username, password)
  
  # 5. Data fetch
  if session_ok or args.force:
    target = date.fromisoformat(args.date) if args.date else date.today() - timedelta(days=1)
    await test_data_fetch(client, target)
  else:
    print('\n Skipping data fetch (session not valid). Use --force to try anyway.')

  # 6. HDO (always works, no auth needed)
  await test_hdo('259', '246')


  # Summary
  print_section('Summary')
  print(f' Cookie file:  {"" if cookies_ok else ""}')
  print(f' Session valid: {"" if session_ok else "— refresh cookies"}')
  print(f'\n Test data saved in: {TEST_DATA_DIR}/')
  print(' Check raw_response_*.json to see what the portal API returns')
  print(' Check response_*.html to inspect portal page structure')
  if (TEST_DATA_DIR / 'portal_screenshot.png').exists():
    print(f' Check portal_screenshot.png to see what Playwright rendered')
  print()


def main():
  parser = argparse.ArgumentParser(description='ZSDIS addon local tester')
  parser.add_argument('--username', '-u', default='', help='diportal.sk username')
  parser.add_argument('--password', '-p', default='', help='diportal.sk password')
  parser.add_argument('--date', '-d', default='', help='Date to fetch (YYYY-MM-DD), default=yesterday')
  parser.add_argument('--debug', action='store_true', help='Enable verbose debug logging')
  parser.add_argument('--discover', action='store_true', help='Run Playwright discovery to find API endpoint')
  parser.add_argument('--force', action='store_true', help='Attempt data fetch even if session appears invalid')
  args = parser.parse_args()

  try:
    asyncio.run(run_tests(args))
  except KeyboardInterrupt:
    print('\nStopped.')


if __name__ == '__main__':
  main()
