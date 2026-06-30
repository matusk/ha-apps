#!/usr/bin/env python3
"""
Home Assistant integration — posts sensor states and historical statistics
via the HA Supervisor REST API.
"""

import logging
import os
from datetime import datetime, timezone, timedelta, time
from typing import Optional

import aiohttp
import urllib.parse

logger = logging.getLogger(__name__)

HA_BASE = 'http://supervisor/core'


class HomeAssistantIntegration:
  def __init__(self):
    self.token = os.environ.get('SUPERVISOR_TOKEN', os.environ.get('HASSIO_TOKEN', ''))
    self.base_url = HA_BASE
    if not self.token:
      logger.warning("SUPERVISOR_TOKEN not set — HA API calls will fail")
    self._session = None

  @property
  def _headers(self) -> dict:
    return {
      'Content-Type': 'application/json',
      'Authorization': f'Bearer {self.token}',
    }

  async def _get_session(self) -> aiohttp.ClientSession:
    if self._session is None or self._session.closed:
      self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
    return self._session

  async def close(self):
    if self._session and not self._session.closed:
      await self._session.close()

  async def disable_force_reimport(self) -> bool:
    """Set force_reimport to false via the Supervisor API so the App doesn't loop on restart."""
    url = 'http://supervisor/addons/self/options'
    info_url = 'http://supervisor/addons/self/info'
    try:
      session = await self._get_session()
      
      # First fetch current options
      async with session.get(info_url, headers=self._headers) as resp:
        if resp.status not in (200, 201):
            return False
        data = await resp.json()
        current_options = data.get('data', {}).get('options', {})

      # Update force_reimport
      current_options['force_reimport'] = False
      payload = {"options": current_options}

      # POST back
      async with session.post(url, headers=self._headers, json=payload) as resp:
        ok = resp.status in (200, 201)
        if not ok:
          body = await resp.text()
          logger.error(f"Supervisor API error {resp.status} updating options: {body[:200]}")
        return ok
    except Exception as e:
      logger.error(f"Supervisor API connection error: {e}")
      return False

  async def _post(self, path: str, payload: dict) -> bool:
    """POST to HA Supervisor API."""
    url = f'{self.base_url}{path}'
    try:
      session = await self._get_session()
      async with session.post(url, headers=self._headers, json=payload) as resp:
        ok = resp.status in (200, 201)
        if not ok:
          body = await resp.text()
          logger.error(f"HA API error {resp.status} for {path}: {body[:200]}")
        return ok
    except Exception as e:
      logger.error(f"HA API connection error ({url}): {e}")
      return False

  async def _get(self, path: str) -> Optional[dict]:
    """GET from HA Supervisor API. Returns parsed JSON or None on error."""
    url = f'{self.base_url}{path}'
    try:
      session = await self._get_session()
      async with session.get(url, headers=self._headers) as resp:
        if resp.status == 200:
          return await resp.json()
        else:
          body = await resp.text()
          logger.error(f"HA API error {resp.status} for {path}: {body[:200]}")
          return None
    except Exception as e:
      logger.error(f"HA API connection error ({url}): {e}")
      return None

  async def set_state(
    self,
    entity_id: str,
    state,
    attributes: Optional[dict] = None,
  ) -> bool:
    """Set a sensor state in HA via the States API."""
    payload: dict = {'state': str(state)}
    if attributes:
      payload['attributes'] = attributes
    ok = await self._post(f'/api/states/{entity_id}', payload)
    if ok:
      logger.debug(f"{entity_id} = {state}")
    return ok


  # ─── Schedule helper integration ─────────────────────────────────────────

  async def sync_schedule_helper(self, name: str, icon: str, intervals: list[tuple[time, time]]) -> Optional[str]:
    """
    Connect to HA WebSocket API to create or update a schedule helper.
    intervals: list of (start_time, end_time) representing the NT windows.
    Returns the entity_id of the helper, or None on failure.
    """
    if not self.token:
      return None

    # Format the days dictionary
    day_schedule = []
    for start, end in intervals:
      day_schedule.append({"from": start.strftime("%H:%M:%S"), "to": end.strftime("%H:%M:%S")})
    
    schedule_data = {day: day_schedule for day in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']}
    schedule_data['name'] = name
    schedule_data['icon'] = icon

    try:
      session = await self._get_session()
      async with session.ws_connect('ws://supervisor/core/api/websocket') as ws:
        msg = await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": self.token})
        msg = await ws.receive_json()
        if msg.get('type') != 'auth_ok':
          logger.error(f"WS auth failed: {msg}")
          return None
        
        await ws.send_json({"id": 1, "type": "schedule/list"})
        msg = await ws.receive_json()
        existing = msg.get('result', [])
        
        target_id = None
        for item in existing:
          if item.get('name') == name:
            target_id = item.get('id')
            break
        
        if target_id:
          payload = {"id": 2, "type": "schedule/update", "schedule_id": target_id}
          payload.update(schedule_data)
        else:
          payload = {"id": 2, "type": "schedule/create"}
          payload.update(schedule_data)
          
        await ws.send_json(payload)
        resp = await ws.receive_json()
        if not resp.get('success'):
          logger.error(f"Failed to sync schedule helper {name}: {resp}")
          return None
        
        result_id = resp.get('result', {}).get('id')
        if result_id:
          return f"schedule.{result_id}"
        return None
    except Exception as e:
      logger.error(f"Error syncing schedule helper {name}: {e}")
      return None

  async def get_schedule_state(self, entity_id: str) -> Optional[str]:
    if not entity_id:
      return None
    data = await self._get(f'/api/states/{entity_id}')
    if data is None:
      return None
    state = data.get('state')
    if state not in ('on', 'off'):
      logger.warning(f"Schedule entity {entity_id!r} returned unexpected state: {state!r}")
      return None
    return state

  async def get_schedule_intervals(self, entity_id: str) -> list[tuple[time, time]]:
    if not entity_id:
      return []

    end_time = datetime.now(tz=timezone.utc)
    start_time = end_time - timedelta(hours=25)

    path = (
      f'/api/history/period/{urllib.parse.quote(start_time.isoformat())}'
      f'?filter_entity_id={entity_id}'
      f'&end_time={urllib.parse.quote(end_time.isoformat())}'
      f'&minimal_response=true'
      f'&no_attributes=true'
    )

    data = await self._get(path)
    if not data or not isinstance(data, list) or not data[0]:
      logger.warning(f"No history found for schedule entity {entity_id!r}.")
      return []

    records = data[0]
    transitions: list[tuple[str, time]] = []
    for record in records:
      state = record.get('state')
      last_changed = record.get('last_changed')
      if state not in ('on', 'off') or not last_changed:
        continue
      try:
        dt = datetime.fromisoformat(last_changed)
        local_time = dt.astimezone().time().replace(second=0, microsecond=0)
        transitions.append((state, local_time))
      except (ValueError, TypeError):
        continue

    if not transitions:
      return []

    intervals: list[tuple[time, time]] = []
    on_start: Optional[time] = None

    for state, t in transitions:
      if state == 'on' and on_start is None:
        on_start = t
      elif state == 'off' and on_start is not None:
        intervals.append((on_start, t))
        on_start = None

    if on_start is not None:
      current_data = await self._get(f'/api/states/{entity_id}')
      if current_data:
        next_event_str = current_data.get('attributes', {}).get('next_event')
        if next_event_str:
          try:
            next_event = datetime.fromisoformat(next_event_str).astimezone().time().replace(
              second=0, microsecond=0
            )
            intervals.append((on_start, next_event))
          except (ValueError, TypeError):
            pass

    return intervals

  # ─── Energy sensors ───────────────────────────────────────────────────────

  async def publish_energy_sensors(
    self,
    date_str: str,
    total_kwh: float,
    vt_kwh: float,
    nt_kwh: float,
    cost_eur: float,
    monthly_kwh: Optional[float],
    tariff_high_rate: float,
    tariff_low_rate: float,
  ) -> bool:
    """
    Publish all energy-related sensors for a given date.

    date_str: ISO date string, e.g. '2026-06-25'
    vt_kwh / nt_kwh: high/low tariff consumption derived from HA schedule helper
    monthly_kwh: current-month billed consumption (optional, from portal history API)
    """
    ts = datetime.now(tz=timezone.utc).isoformat()
    success = True

    sensors = [
      (
        'sensor.zsdis_yesterday_total',
        round(total_kwh, 3),
        {
          'friendly_name': 'ZSDIS Yesterday Total',
          'unit_of_measurement': 'kWh',
          'device_class': 'energy',
          'state_class': 'total',
          'icon': 'mdi:lightning-bolt',
          'data_date': date_str,
          'last_update': ts,
          'source': 'diportal.sk',
        },
      ),
      (
        'sensor.zsdis_yesterday_vt',
        round(vt_kwh, 3),
        {
          'friendly_name': 'ZSDIS Yesterday VT (high tariff)',
          'unit_of_measurement': 'kWh',
          'device_class': 'energy',
          'state_class': 'total',
          'icon': 'mdi:lightning-bolt-circle',
          'data_date': date_str,
          'last_update': ts,
        },
      ),
      (
        'sensor.zsdis_yesterday_nt',
        round(nt_kwh, 3),
        {
          'friendly_name': 'ZSDIS Yesterday NT (low tariff)',
          'unit_of_measurement': 'kWh',
          'device_class': 'energy',
          'state_class': 'total',
          'icon': 'mdi:lightning-bolt-outline',
          'data_date': date_str,
          'last_update': ts,
        },
      ),
      (
        'sensor.zsdis_yesterday_cost',
        round(cost_eur, 4),
        {
          'friendly_name': 'ZSDIS Yesterday Cost',
          'unit_of_measurement': 'EUR',
          'device_class': 'monetary',
          'state_class': 'total',
          'icon': 'mdi:currency-eur',
          'high_rate_eur': round(tariff_high_rate, 4),
          'low_rate_eur': round(tariff_low_rate, 4),
          'vt_cost': round(vt_kwh * tariff_high_rate, 4),
          'nt_cost': round(nt_kwh * tariff_low_rate, 4),
          'data_date': date_str,
          'last_update': ts,
        },
      ),
      (
        'sensor.zsdis_last_sync',
        ts,
        {
          'friendly_name': 'ZSDIS Last Sync',
          'device_class': 'timestamp',
          'icon': 'mdi:sync',
          'last_data_date': date_str,
        },
      ),
    ]

    if monthly_kwh is not None:
      sensors.append((
        'sensor.zsdis_monthly_consumption',
        round(monthly_kwh, 3),
        {
          'friendly_name': 'ZSDIS Monthly Consumption',
          'unit_of_measurement': 'kWh',
          'device_class': 'energy',
          'state_class': 'total',
          'icon': 'mdi:calendar-month',
          'last_update': ts,
        }
      ))

    for entity_id, state, attrs in sensors:
      ok = await self.set_state(entity_id, state, attrs)
      success = success and ok

    return success

  async def publish_tariff_sensors(
    self,
    current_tariff: str,
    next_switch: str,
    hdo_primary_active: bool,
    primary_code: str,
  ) -> bool:
    """
    Publish HDO-based tariff sensors.

    current_tariff: 'high' or 'low'
    next_switch: 'HH:MM' string of the next tariff switch, or 'unknown'
    """
    ts = datetime.now(tz=timezone.utc).isoformat()
    success = True
    sensors = []

    if primary_code:
      sensors.append((
        'sensor.zsdis_current_tariff',
        current_tariff,
        {
          'friendly_name': f'ZSDIS Current Tariff ({primary_code})',
          'icon': 'mdi:transmission-tower' if current_tariff == 'high' else 'mdi:transmission-tower-off',
          'hdo_code': primary_code,
          'next_switch': next_switch,
          'last_update': ts,
        },
      ))

    for entity_id, state, attrs in sensors:
      ok = await self.set_state(entity_id, state, attrs)
      success = success and ok

    return success

  # ─── Historical statistics ────────────────────────────────────────────────

  async def get_latest_statistic_sum(self, entity_id: str) -> float:
    """
    Query HA recorder for the most recent cumulative sum of a statistic sensor.
    Returns 0.0 if no statistics exist yet (first import).
    """
    try:
      session = await self._get_session()
      async with session.ws_connect('ws://supervisor/core/api/websocket') as ws:
        msg = await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": self.token})
        msg = await ws.receive_json()
        if msg.get('type') != 'auth_ok':
          return 0.0

        # Get the last 2 days of statistics to find the latest sum
        end_dt = datetime.now(tz=timezone.utc)
        start_dt = end_dt - timedelta(days=2)
        await ws.send_json({
          'id': 1,
          'type': 'recorder/statistics_during_period',
          'start_time': start_dt.isoformat(),
          'end_time': end_dt.isoformat(),
          'statistic_ids': [entity_id],
          'period': 'hour',
          'types': ['sum'],
        })
        resp = await ws.receive_json()
        stats = resp.get('result', {}).get(entity_id, [])
        if stats:
          return float(stats[-1].get('sum') or 0.0)
    except Exception as e:
      logger.warning(f"Could not read latest sum for {entity_id}: {e}")
    return 0.0

  async def import_day_statistics(self, day_record: dict) -> bool:
    """
    Import a single day's data into HA recorder statistics, correctly continuing
    the running cumulative sum from whatever was previously stored.

    day_record must contain: {'date': 'YYYY-MM-DD', 'total_kwh': X, 'vt_kwh': Y,
                              'nt_kwh': Z, 'intervals': [...]}
    """
    if not day_record or not self.token:
      return False

    sensor_map = {
      'sensor.zsdis_yesterday_total': 'total_kwh',
      'sensor.zsdis_yesterday_vt': 'vt_kwh',
      'sensor.zsdis_yesterday_nt': 'nt_kwh',
    }

    all_ok = True

    try:
      session = await self._get_session()
      async with session.ws_connect('ws://supervisor/core/api/websocket') as ws:
        msg = await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": self.token})
        msg = await ws.receive_json()
        if msg.get('type') != 'auth_ok':
          logger.error(f"WS auth failed for daily statistics import: {msg}")
          return False

        # Fetch current cumulative sums for each sensor in one connection
        end_dt = datetime.now(tz=timezone.utc)
        start_dt = end_dt - timedelta(days=2)
        await ws.send_json({
          'id': 1,
          'type': 'recorder/statistics_during_period',
          'start_time': start_dt.isoformat(),
          'end_time': end_dt.isoformat(),
          'statistic_ids': list(sensor_map.keys()),
          'period': 'hour',
          'types': ['sum'],
        })
        sum_resp = await ws.receive_json()
        sum_result = sum_resp.get('result', {})

        msg_id = 2
        for entity_id, field in sensor_map.items():
          # Get current running total
          existing = sum_result.get(entity_id, [])
          base_sum = float(existing[-1].get('sum') or 0.0) if existing else 0.0

          intervals = day_record.get('intervals')
          stats = []

          if intervals:
            # Build hourly stats from 15-min intervals
            hourly_sums: dict[str, float] = {}
            for iv in intervals:
              kwh = float(iv.get('kwh', 0))
              is_nt = iv.get('is_nt', False)
              if field == 'vt_kwh' and is_nt:
                val = 0.0
              elif field == 'nt_kwh' and not is_nt:
                val = 0.0
              else:
                val = kwh
              hour_str = (iv.get('measuredAt') or '00:00:00').split(':')[0]
              hourly_sums[hour_str] = hourly_sums.get(hour_str, 0.0) + val

            cumulative = base_sum
            for hour_str in sorted(hourly_sums.keys()):
              val = hourly_sums[hour_str]
              cumulative += val
              stats.append({
                'start': f"{day_record['date']}T{hour_str}:00:00+00:00",
                'sum': round(cumulative, 3),
                'state': round(val, 3),
              })
          else:
            # Fallback: single daily entry at midnight
            kwh = float(day_record.get(field, 0) or 0)
            stats.append({
              'start': f"{day_record['date']}T00:00:00+00:00",
              'sum': round(base_sum + kwh, 3),
              'state': round(kwh, 3),
            })

          payload = {
            'id': msg_id,
            'type': 'recorder/import_statistics',
            'metadata': {
              'statistic_id': entity_id,
              'name': entity_id.replace('sensor.', '').replace('_', ' ').title(),
              'source': 'recorder',
              'unit_of_measurement': 'kWh',
              'has_mean': False,
              'has_sum': True,
            },
            'stats': stats,
          }
          msg_id += 1

          await ws.send_json(payload)
          resp = await ws.receive_json()
          if not resp.get('success'):
            logger.error(f"Daily stat import failed for {entity_id}: {resp}")
            all_ok = False
          else:
            day_kwh = float(day_record.get(field, 0) or 0)
            logger.info(
              f"  Stats updated: {entity_id} +{day_kwh:.3f} kWh "
              f"(running total: {base_sum + day_kwh:.3f} kWh)"
            )

    except Exception as e:
      logger.error(f"Error importing daily statistics: {e}")
      return False

    return all_ok

  async def import_historical_statistics(self, daily_records: list) -> bool:
    """
    Import historical energy data using HA's recorder statistics import API.
    Backfills the Energy Dashboard without flooding the state log.

    Each record must contain: {'date': 'YYYY-MM-DD', 'total_kwh': X, 'vt_kwh': Y, 'nt_kwh': Z}
    """
    if not daily_records or not self.token:
      return True

    sensor_map = {
      'sensor.zsdis_yesterday_total': 'total_kwh',
      'sensor.zsdis_yesterday_vt': 'vt_kwh',
      'sensor.zsdis_yesterday_nt': 'nt_kwh',
    }

    all_ok = True
    
    try:
      session = await self._get_session()
      async with session.ws_connect('ws://supervisor/core/api/websocket') as ws:
        # Auth
        msg = await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": self.token})
        msg = await ws.receive_json()
        if msg.get('type') != 'auth_ok':
          logger.error(f"WS auth failed for statistics import: {msg}")
          return False

        msg_id = 3
        for entity_id, field in sensor_map.items():
          stats = []
          cumulative = 0.0
          for record in sorted(daily_records, key=lambda r: r['date']):
            intervals = record.get('intervals')
            if not intervals:
              # Fallback to daily total if intervals are missing
              kwh = float(record.get(field, 0) or 0)
              cumulative += kwh
              dt_str = f"{record['date']}T00:00:00+00:00"
              stats.append({
                'start': dt_str,
                'sum': round(cumulative, 3),
                'state': round(kwh, 3),
              })
              continue

            hourly_sums = {}
            for interval in intervals:
              kwh = float(interval.get('kwh', 0))
              is_nt = interval.get('is_nt', False)
              
              if field == 'vt_kwh' and is_nt:
                val = 0.0
              elif field == 'nt_kwh' and not is_nt:
                val = 0.0
              else:
                val = kwh
              
              time_str = interval.get('measuredAt', '00:00:00')
              hour_str = time_str.split(':')[0]
              
              if hour_str not in hourly_sums:
                  hourly_sums[hour_str] = 0.0
              hourly_sums[hour_str] += val

            for hour_str in sorted(hourly_sums.keys()):
              val = hourly_sums[hour_str]
              cumulative += val
              dt_str = f"{record['date']}T{hour_str}:00:00+00:00"
              stats.append({
                'start': dt_str,
                'sum': round(cumulative, 3),
                'state': round(val, 3),
              })

          payload = {
            'id': msg_id,
            'type': 'recorder/import_statistics',
            'metadata': {
              'statistic_id': entity_id,
              'name': entity_id.replace('sensor.', '').replace('_', ' ').title(),
              'source': 'recorder',
              'unit_of_measurement': 'kWh',
              'has_mean': False,
              'has_sum': True,
            },
            'stats': stats,
          }
          msg_id += 1

          await ws.send_json(payload)
          resp = await ws.receive_json()
          
          if not resp.get('success'):
            logger.error(f"Historical import failed for {entity_id}: {resp}")
            all_ok = False
          else:
            logger.info(f"  Historical import OK: {entity_id} ({len(stats)} days)")

    except Exception as e:
      logger.error(f"Error connecting to WS for historical import: {e}")
      return False

    return all_ok