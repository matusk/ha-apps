#!/usr/bin/env python3
"""
ZSDIS Diportal Client — handles authentication and data fetching from www.diportal.sk
Uses cookie-based session management; cookies must be bootstrapped manually.
"""

import asyncio
import json
import logging
import ssl
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional
import hashlib
from http.cookiejar import MozillaCookieJar
import aiohttp

logger = logging.getLogger(__name__)

BASE_URL = 'https://www.diportal.sk'
SESSION_FILE = Path(os.environ.get('SESSION_FILE', '/data/session.json'))

INTERVAL_DATA_API = f'{BASE_URL}/portal/api/interval-data/getProfileData'
METERING_POINTS_API = f'{BASE_URL}/portal/api/customer/odbernemesta'
MONTHLY_CONSUMPTION_API = f'{BASE_URL}/portal/api/history-consumption/list'


def _make_ssl_ctx() -> ssl.SSLContext:
    """Create an SSL context that accepts the diportal.sk self-signed cert."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class ZSDISClient:
    def __init__(
        self, 
        cookie_string: str,
        twocaptcha_api_key: str = "",
        portal_username: str = "",
        portal_password: str = ""
    ):
        self.cookie_string = (cookie_string or "").strip()
        self.twocaptcha_api_key = (twocaptcha_api_key or "").strip()
        self.portal_username = (portal_username or "").strip()
        self.portal_password = (portal_password or "").strip()
        self.cookies: dict = {}
        self.config_cookie_hash = hashlib.sha256(self.cookie_string.encode('utf-8')).hexdigest() if self.cookie_string else ""
        self.saved_hash = None

    # ─── Session persistence ──────────────────────────────────────────────────

    def _load_session(self) -> bool:
        """Load persisted cookies and hash from /data/session.json."""
        if SESSION_FILE.exists():
            try:
                data = json.loads(SESSION_FILE.read_text())
                self.cookies = data.get('cookies', {})
                self.saved_hash = data.get('config_cookie_hash', "")
                logger.info(f"Session loaded ({len(self.cookies)} cookies)")
                return bool(self.cookies)
            except Exception as e:
                logger.error(f"Failed to load session: {e}")
        return False

    def _save_session(self):
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.write_text(json.dumps({
            'cookies': self.cookies,
            'config_cookie_hash': self.config_cookie_hash,
            'saved_at': datetime.now().isoformat(),
        }, indent=2))
        os.chmod(SESSION_FILE, 0o600)  # owner-read/write only
        logger.info("Session saved to session.json")

    def _parse_cookie_string(self, raw: str) -> dict:
        parsed = {}
        for chunk in raw.split(';'):
            chunk = chunk.strip()
            if not chunk: continue
            if '=' in chunk:
                k, v = chunk.split('=', 1)
                parsed[k.strip()] = v.strip()
            else:
                parsed[chunk] = ""
        return parsed

    def _try_bootstrap_from_config(self) -> bool:
        if not self.cookie_string:
            return False

        if self.config_cookie_hash == self.saved_hash and self.cookies:
            # The string in config hasn't changed. If we are calling this,
            # it means the session is expired, so re-parsing the same string won't help.
            return False

        parsed = self._parse_cookie_string(self.cookie_string)
        if not parsed:
            logger.error("Failed to parse the provided cookie_string in configuration.")
            return False

        self.cookies = parsed
        self.saved_hash = self.config_cookie_hash
        self._save_session()
        logger.info(f"Loaded {len(self.cookies)} cookies from configuration string!")
        return True

    # ─── HTTP helpers ─────────────────────────────────────────────────────────

    def _build_cookie_header(self) -> str:
        return '; '.join(f'{k}={v}' for k, v in self.cookies.items())

    def _base_headers(self) -> dict:
        """Standard headers for all diportal API requests."""
        return {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            ),
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'sk-SK,sk;q=0.9,cs;q=0.8,en;q=0.7',
            'Origin': BASE_URL,
            'Referer': f'{BASE_URL}/portal/',
            'X-Requested-With': 'XMLHttpRequest',
            # The portal does not validate CSRF tokens for the API endpoints we use,
            # so a placeholder value is sufficient.
            'X-CSRF': 'DUMMY',
            'Content-Type': 'application/json',
            'Cookie': self._build_cookie_header(),
        }

    # ─── Authentication ───────────────────────────────────────────────────────

    async def is_session_valid(self) -> bool:
        """
        Check if current cookies give an authenticated portal session.
        A 200, 400, or 417 response means auth worked (unauthed returns 302/401).
        """
        payload = {
            "filter": {
                "deliveryPointId": "dummy",
                "from": date.today().strftime('%Y-%m-%d'),
                "to": date.today().strftime('%Y-%m-%d'),
                "profileRole": None,
                "loadProfileRoles": True,
            },
            "businessPartnerId": "dummy",
            "businessRoleId": "KZ",
            "source": "KOC",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    INTERVAL_DATA_API,
                    headers=self._base_headers(),
                    json=payload,
                    ssl=_make_ssl_ctx(),
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    logger.debug(f"Session check: HTTP {resp.status}")
                    if resp.status in (200, 400, 417):
                        logger.info(f"Session valid (HTTP {resp.status})")
                        return True
                    elif resp.status in (302, 401, 403, 405):
                        logger.info(f"Session expired (HTTP {resp.status})")
                        return False
                    else:
                        logger.warning(f"Session check unexpected status: {resp.status}")
                        return False
        except Exception as e:
            logger.error(f"Session check error: {e}")
        return False

    async def ensure_authenticated(self) -> bool:
        """
        Ensures a valid session using a priority cascade:
        1. Existing /data/session.json
        2. cookie_string from configuration UI
        """
        if self._load_session() and await self.is_session_valid():
            logger.info("Existing session is valid")
            return True

        if self._try_bootstrap_from_config() and await self.is_session_valid():
            logger.info("Config cookies are valid")
            return True

        if self.twocaptcha_api_key and self.portal_username and self.portal_password:
            logger.info("Attempting auto-login via 2Captcha...")
            if await self._perform_auto_login():
                return True
            else:
                logger.error("Auto-login failed.")

        logger.warning(
            "Session is expired or missing. Steps to get fresh cookies:\n"
            " 1. Open Chrome → go to https://www.diportal.sk\n"
            " 2. Log in manually\n"
            " 3. Press F12 to open Developer Tools → Network tab\n"
            " 4. Refresh the page and click on a request to diportal.sk\n"
            " 5. Find 'Cookie:' under Request Headers, right-click -> Copy Value\n"
            " 6. Paste it into the 'cookie_string' field in the Add-on Configuration\n"
            " 7. Save and Restart the add-on\n"
        )
        return False

    async def wait_for_bootstrap_and_authenticate(self) -> bool:
        """Block forever since recovery requires a config change and restart."""
        logger.info("Waiting for add-on restart with new cookie_string...")
        while True:
            await asyncio.sleep(3600)

    # ─── Auto-Login via 2Captcha ──────────────────────────────────────────────

    async def _solve_captcha(self) -> Optional[str]:
        site_key = "6LfdaLUZAAAAAHCVziF1iDFvmCxAjlIP1xF6pjHg"
        url = BASE_URL
        api_url = "https://2captcha.com/in.php"
        res_url = "https://2captcha.com/res.php"
        
        try:
            async with aiohttp.ClientSession() as session:
                params = {
                    "key": self.twocaptcha_api_key,
                    "method": "userrecaptcha",
                    "googlekey": site_key,
                    "pageurl": url,
                    "json": 1,
                    "invisible": 1
                }
                async with session.post(api_url, data=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    res = await resp.json()
                    if res.get("status") != 1:
                        logger.error(f"2Captcha submission failed: {res}")
                        return None
                    request_id = res.get("request")
                
                logger.info(f"2Captcha task submitted (ID: {request_id}). Waiting 15s before polling...")
                await asyncio.sleep(15)
                
                poll_params = {
                    "key": self.twocaptcha_api_key,
                    "action": "get",
                    "id": request_id,
                    "json": 1
                }
                
                for _ in range(24):  # Poll up to 24 times (120 seconds)
                    async with session.get(res_url, params=poll_params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        res = await resp.json()
                        if res.get("status") == 1:
                            logger.info("2Captcha solved successfully!")
                            return res.get("request")
                        elif res.get("request") == "CAPCHA_NOT_READY":
                            await asyncio.sleep(5)
                        else:
                            logger.error(f"2Captcha failed: {res}")
                            return None
                            
                logger.error("2Captcha timeout (took too long to solve).")
                return None
        except Exception as e:
            logger.error(f"2Captcha error: {e}")
            return None

    async def _perform_auto_login(self) -> bool:
        token = await self._solve_captcha()
        if not token:
            return False
            
        login_url = f"{BASE_URL}/portal/api/security/login"
        payload = {
            "userId": self.portal_username,
            "password": self.portal_password,
            "invisibleRecaptchaResponse": token
        }
        
        try:
            # We use a fresh session to cleanly capture the Set-Cookie headers
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    login_url,
                    json=payload,
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        'Accept': 'application/json, text/plain, */*',
                        'Origin': BASE_URL,
                        'Referer': f'{BASE_URL}/portal/',
                        'Content-Type': 'application/json',
                    },
                    ssl=_make_ssl_ctx(),
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status == 200:
                        # Extract cookies set by the portal
                        cookies = session.cookie_jar.filter_cookies(login_url)
                        if cookies:
                            self.cookies = {name: cookie.value for name, cookie in cookies.items()}
                            self._save_session()
                            logger.info("Auto-login successful, cookies extracted and saved!")
                            return True
                        else:
                            logger.error("Auto-login returned 200 but no cookies were found.")
                    else:
                        text = await resp.text()
                        # Truncate to avoid leaking credentials if the portal echoes back the request
                        preview = text[:200].replace('\n', ' ') if text else '(empty)'
                        logger.error(f"Auto-login failed. HTTP {resp.status} — {preview}")
        except Exception as e:
            logger.error(f"Auto-login exception: {e}")
            
        return False

    # ─── ID Discovery ─────────────────────────────────────────────────────────

    async def auto_discover_ids(self) -> tuple[Optional[str], Optional[str]]:
        """
        Fetch metering points from the new portal API and return
        (business_partner_id, delivery_point_id) for the first found entry.
        Returns (None, None) on failure.
        """
        try:
            async with aiohttp.ClientSession() as session:
                # Step 1: Get Business Partner ID
                bp_api = f'{BASE_URL}/portal/api/commons/getBusinessPartnersForUser'
                logger.debug("Auto-discovery step 1: Fetching business partners")
                async with session.get(
                    bp_api,
                    headers=self._base_headers(),
                    ssl=_make_ssl_ctx(),
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"BP discovery failed: HTTP {resp.status}")
                        return None, None
                        
                    data = await resp.json(content_type=None)
                    bps = data.get('data', [])
                    if not bps:
                        logger.error("BP discovery: no business partners returned")
                        return None, None
                        
                    # Find the first valid BP
                    bp_id = None
                    bp_role = 'KZ'
                    for bp in bps:
                        if bp.get('businessPartnerId'):
                            bp_id = str(bp['businessPartnerId'])
                            # Usually KZ, but we can extract it if present
                            if bp.get('businessRoleIds') and len(bp['businessRoleIds']) > 0:
                                bp_role = bp['businessRoleIds'][0]
                            break
                            
                    if not bp_id:
                        logger.error("BP discovery: businessPartnerId not found in response")
                        return None, None
                        
                # Step 2: Get Delivery Point ID for that BP
                dp_api = f'{BASE_URL}/portal/api/delivery-points-list/loadDeliveryPoints'
                payload = {
                    "filter": {
                        "onlyActive": True,
                        "smartMeterEnabled": None,
                        "dateFrom": "",
                        "dateTo": ""
                    },
                    "businessPartnerId": bp_id,
                    "businessRoleId": bp_role
                }
                
                logger.debug(f"Auto-discovery step 2: Fetching delivery points for BP {bp_id}")
                async with session.post(
                    dp_api,
                    headers=self._base_headers(),
                    json=payload,
                    ssl=_make_ssl_ctx(),
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"DP discovery failed: HTTP {resp.status}")
                        return None, None
                        
                    data = await resp.json(content_type=None)
                    points = data.get('data', [])
                    if not points:
                        logger.error("DP discovery: no delivery points returned")
                        return None, None
                        
                    dp_id = None
                    for point in points:
                        if point.get('deliveryPointId'):
                            dp_id = str(point['deliveryPointId'])
                            break
                            
                    if bp_id and dp_id:
                        logger.info(f"Discovered: businessPartnerId={bp_id}, deliveryPointId={dp_id}")
                        return bp_id, dp_id
                        
                    logger.error("DP discovery: deliveryPointId not found in response")
                    return None, None
                    
        except Exception as e:
            logger.error(f"ID discovery error: {e}")
            return None, None

    # ─── Data fetching ────────────────────────────────────────────────────────

    async def fetch_day(
        self,
        target_date: date,
        hdo_nt_intervals: Optional[list] = None,
    ) -> dict:
        """
        Fetch 15-minute interval data for a specific date.
        Requires DELIVERY_POINT_ID and BUSINESS_PARTNER_ID env vars.

        hdo_nt_intervals: list of (start_time, end_time) tuples when NT is active.
        If provided, consumption is split into vt_kwh / nt_kwh using the HDO schedule.
        If None, all consumption is reported as vt_kwh with nt_kwh = 0.
        """
        if not await self.is_session_valid():
            logger.error("Session expired, cannot fetch data")
            return {}

        delivery_point_id = os.environ.get('DELIVERY_POINT_ID')
        business_partner_id = os.environ.get('BUSINESS_PARTNER_ID')
        business_role_id = os.environ.get('BUSINESS_ROLE_ID', 'KZ')
        source = os.environ.get('SOURCE', 'KOC')

        if not delivery_point_id or not business_partner_id:
            logger.error("DELIVERY_POINT_ID or BUSINESS_PARTNER_ID missing from env")
            return {}

        date_str = target_date.strftime('%Y-%m-%d')
        payload = {
            "filter": {
                "deliveryPointId": delivery_point_id,
                "from": date_str,
                "to": date_str,
                "profileRole": None,
                "loadProfileRoles": True,
            },
            "businessPartnerId": business_partner_id,
            "businessRoleId": business_role_id,
            "source": source,
        }

        try:
            async with aiohttp.ClientSession() as session:
                logger.info(f"Fetching consumption for {date_str}")
                async with session.post(
                    INTERVAL_DATA_API,
                    headers=self._base_headers(),
                    json=payload,
                    ssl=_make_ssl_ctx(),
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        # Save raw response for debugging
                        raw_dir = Path('/data/raw')
                        raw_dir.mkdir(parents=True, exist_ok=True)
                        (raw_dir / f'raw_response_{date_str}.json').write_text(
                            json.dumps(data, indent=2, ensure_ascii=False)
                        )
                        return self._parse_profile_data(data, target_date, hdo_nt_intervals)
                    else:
                        logger.error(f"Failed to fetch data: HTTP {resp.status}")
                        return {}
        except Exception as e:
            logger.error(f"Error fetching interval data: {e}")
            return {}

    def _parse_profile_data(
        self,
        data: dict,
        target_date: date,
        hdo_nt_intervals: Optional[list] = None,
    ) -> dict:
        """
        Parse the raw JSON API response into HA-ready statistics.

        The portal returns 15-minute intervals with measuredValue (kW) and,
        at each full hour, an hourlyConsumption (kWh) summary field.
        We use measuredValue * 0.25 for each interval to get per-slot kWh.

        VT/NT split: if hdo_nt_intervals is provided, each interval's timestamp
        is checked against the HDO schedule to classify as NT (low tariff) or VT
        (high tariff). Otherwise all consumption is treated as VT.
        """
        try:
            root = data.get('data', data)
            profile_data = root.get('profileData', {})
            daily_data = profile_data.get('dailyIntervalData', [])

            if not daily_data:
                logger.warning("No dailyIntervalData found in response")
                return {}

            date_str = target_date.strftime('%Y-%m-%d')
            day_entry = next(
                (e for e in daily_data if e.get('date') == date_str), None
            )
            if not day_entry:
                logger.warning(f"No data found for {date_str}")
                return {}

            intervals = day_entry.get('values', [])
            if not intervals:
                logger.warning(f"No interval values found for {date_str}")
                return {}

            # Use the authoritative daily total if present
            total_kwh = float(day_entry.get('consumption') or 0.0)

            vt_kwh = 0.0
            nt_kwh = 0.0

            enriched_intervals = []
            for v in intervals:
                kwh = float(v.get('measuredValue', 0.0)) * 0.25  # 15 min = 0.25 h
                is_nt = False
                if hdo_nt_intervals and v.get('measuredAt'):
                    slot_time = datetime.strptime(v['measuredAt'], '%H:%M:%S').time()
                    if _is_nt_active(hdo_nt_intervals, slot_time):
                        is_nt = True
                        nt_kwh += kwh
                    else:
                        vt_kwh += kwh
                else:
                    vt_kwh += kwh
                
                enriched_intervals.append({
                    'measuredAt': v.get('measuredAt'),
                    'kwh': kwh,
                    'is_nt': is_nt
                })

            # If authoritative total is missing, derive from intervals
            if total_kwh == 0.0:
                total_kwh = vt_kwh + nt_kwh

            return {
                'date': date_str,
                'total_kwh': round(total_kwh, 3),
                'vt_kwh': round(vt_kwh, 3),
                'nt_kwh': round(nt_kwh, 3),
                'intervals': enriched_intervals,
            }
        except Exception as e:
            logger.error(f"Error parsing profile data: {e}")
            return {}

    async def fetch_monthly_consumption(self) -> list:
        """
        Fetch monthly aggregated consumption history for the current delivery point.
        Returns a list of monthly records, most recent first.
        """
        delivery_point_id = os.environ.get('DELIVERY_POINT_ID')
        business_partner_id = os.environ.get('BUSINESS_PARTNER_ID')

        if not delivery_point_id or not business_partner_id:
            logger.error("DELIVERY_POINT_ID or BUSINESS_PARTNER_ID missing from env")
            return []

        payload = {
            "businessPartnerId": business_partner_id,
            "businessRoleId": "KZ",
            "filter": {
                "deliveryPointIds": [delivery_point_id],
            },
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    MONTHLY_CONSUMPTION_API,
                    headers=self._base_headers(),
                    json=payload,
                    ssl=_make_ssl_ctx(),
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        return data.get('data', {}).get('consumptionHistory', [])
                    else:
                        logger.warning(f"Monthly consumption fetch failed: HTTP {resp.status}")
        except Exception as e:
            logger.error(f"Error fetching monthly consumption: {e}")

        return []

    async def fetch_range(
        self,
        from_date: date,
        to_date: date,
        hdo_nt_intervals: Optional[list] = None,
    ) -> list:
        """Fetch data for a date range (for historical import). Rate-limited."""
        results = []
        current = from_date
        while current <= to_date:
            data = await self.fetch_day(current, hdo_nt_intervals)
            if data and data.get('total_kwh', 0) > 0:
                results.append(data)
                logger.info(
                    f"  {current}: {data['total_kwh']:.3f} kWh "
                    f"(VT={data['vt_kwh']:.3f}, NT={data['nt_kwh']:.3f})"
                )
            else:
                logger.debug(f"  {current}: no data / zero")
            current += timedelta(days=1)
            await asyncio.sleep(1.5)  # be polite
        return results


# ─── HDO helper (module-level, used by _parse_profile_data) ──────────────────

def _is_nt_active(intervals: list, check_time: time) -> bool:
    """Return True if check_time falls within any NT (low tariff) window."""
    for start, end in intervals:
        if start <= end:
            if start <= check_time < end:
                return True
        else:
            # Overnight interval, e.g. 22:00–06:00
            if check_time >= start or check_time < end:
                return True
    return False
