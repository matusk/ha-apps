#!/usr/bin/env python3
"""
HDO (Hromadné Diaľkové Ovládanie) parser.
Fetches the current tariff switching schedule from the public ZSDIS website.
No login required.

Configured HDO codes (e.g. 259, 246) are passed in at construction time.
Primary code → main tariff sensor (VT/NT switch times for all devices)
Water heater code → boiler/water heater circuit
"""

import asyncio
import logging
import re
import ssl
from datetime import datetime, time, timedelta, date
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HDO_PAGE_URL = 'https://www.zsdis.sk/Uvod/Online-sluzby/Casy-prepinania-nizkej-a-vysokej-tarify'
# Fallback: sometimes the schedule is on a different ZSDIS page
HDO_PAGE_URL_ALT = 'https://www.zsdis.sk/sk/distribucna-siet/hdo'


class HDOParser:
  def __init__(self, primary_code: str = '', water_heater_code: str = ''):
    self.primary_code = str(primary_code).strip()
    self.water_heater_code = str(water_heater_code).strip()
    self._schedule_cache: Optional[dict] = None
    self._cache_date: Optional[date] = None
    logger.info(f"HDO Parser: primary={self.primary_code}, water_heater={self.water_heater_code}")

  async def _fetch_schedule_page(self) -> Optional[str]:
    """Fetch the ZSDIS HDO schedule page HTML."""
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    headers = {
      'User-Agent': 'Mozilla/5.0 (compatible; HomeAssistant ZSDIS App)',
      'Accept-Language': 'sk-SK,sk;q=0.9',
    }

    for url in [HDO_PAGE_URL, HDO_PAGE_URL_ALT]:
      try:
        async with aiohttp.ClientSession() as session:
          async with session.get(
            url,
            headers=headers,
            ssl=ssl_ctx,
            timeout=aiohttp.ClientTimeout(total=20),
          ) as resp:
            if resp.status == 200:
              return await resp.text()
      except Exception as e:
        logger.debug(f"HDO fetch from {url}: {e}")
    return None

  def _parse_schedule(self, html: str, hdo_code: str) -> list[tuple[time, time]]:
    """
    Parse the ZSDIS HDO schedule JS objects for a given HDO code.
    Returns list of (start_time, end_time) tuples when NT (low tariff) is active.
    """
    intervals = []

    pattern = re.compile(rf"code:\s*['\"]?{hdo_code}['\"]?,\s*intervals:\s*\[(.*?)\]", re.DOTALL | re.IGNORECASE)
    match = pattern.search(html)

    if not match:
      logger.warning(f"HDO code {hdo_code!r} not found in schedule page — no NT intervals will be applied")
      return []

    intervals_str = match.group(1)
    
    # Format: t_from: '22:00', t_to: '06:00'
    time_pattern = re.compile(r"t_from:\s*['\"](\d{1,2}:\d{2})['\"],\s*t_to:\s*['\"](\d{1,2}:\d{2})['\"]")
    matches = time_pattern.findall(intervals_str)

    for start_str, end_str in matches:
      try:
        start = datetime.strptime(start_str, '%H:%M').time()
        end = datetime.strptime(end_str, '%H:%M').time()
        intervals.append((start, end))
      except ValueError:
        continue

    if intervals:
      logger.info(f"HDO {hdo_code} schedule: {intervals}")
    return intervals


  def _is_tariff_active(
    self,
    intervals: list[tuple[time, time]],
    check_time: Optional[datetime] = None,
  ) -> tuple[bool, Optional[str]]:
    """
    Determine if NT (low tariff / HDO active) is currently active.
    Returns (is_active, next_switch_time_str).
    """
    now = check_time or datetime.now()
    current_time = now.time()

    for start, end in intervals:
      if start <= end:
        # Normal interval (e.g. 14:00–16:00)
        active = start <= current_time < end
      else:
        # Overnight interval (e.g. 22:00–06:00 crosses midnight)
        active = current_time >= start or current_time < end

      if active:
        return True, end.strftime('%H:%M')

    # Not active — find the next start time
    upcoming = []
    for start, end in intervals:
      today = now.date()
      start_dt = datetime.combine(today, start)
      if start_dt < now:
        start_dt += timedelta(days=1)
      upcoming.append(start_dt)

    if upcoming:
      next_dt = min(upcoming)
      return False, next_dt.strftime('%H:%M')

    return False, 'unknown'

  async def get_current_status(self) -> dict:
    """
    Return the current HDO tariff status for both configured codes.
    Schedule is cached per calendar day; fetched fresh each new day.
    """
    today = date.today()
    schedule_source = 'cache'

    if self._cache_date != today or self._schedule_cache is None:
      html = await self._fetch_schedule_page()
      if html:
        schedule_primary = self._parse_schedule(html, self.primary_code)
        schedule_wh = self._parse_schedule(html, self.water_heater_code)
        schedule_source = 'web'
      else:
        logger.warning(
          "Could not fetch HDO schedule from ZSDIS — NT intervals unavailable. "
          "All consumption will be treated as VT until the next successful fetch."
        )
        schedule_primary = []
        schedule_wh = []
        schedule_source = 'unavailable'

      self._schedule_cache = {
        self.primary_code: schedule_primary,
        self.water_heater_code: schedule_wh,
      }
      self._cache_date = today

    schedule_primary = self._schedule_cache[self.primary_code]
    schedule_wh = self._schedule_cache[self.water_heater_code]

    nt_active_primary, next_switch_primary = self._is_tariff_active(schedule_primary)
    nt_active_wh, _ = self._is_tariff_active(schedule_wh)

    # NT active = low tariff; VT active = high tariff
    primary_tariff = 'low' if nt_active_primary else 'high'

    return {
      'primary_tariff': primary_tariff,
      'next_switch': next_switch_primary or 'unknown',
      'water_heater_on': nt_active_wh,
      'hdo_primary_active': nt_active_primary,
      'hdo_water_heater_active': nt_active_wh,
      'schedule_source': schedule_source,
    }
