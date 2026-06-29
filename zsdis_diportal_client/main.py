#!/usr/bin/env python3
"""
ZSDIS Diportal Client — main entry point.
Runs once per day at configured hour, fetches previous day's data,
publishes to HA, and backfills historical records on first run.
"""

import asyncio
import json
import logging
import os
import sys
import random
from datetime import date, datetime, timedelta
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-8s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)

STATE_FILE = Path('/data/state.json')
CONFIG_PATH = Path(os.environ.get('CONFIG_PATH', '/data/options.json'))


# ─── Config ──────────────────────────────────────────────────────────────────

def load_config() -> dict:
    defaults = {
        'cookie_string': '',
        'twocaptcha_api_key': '',
        'portal_username': '',
        'portal_password': '',
        'delivery_point_id': '',
        'business_partner_id': '',
        'hdo_primary': '',
        'hdo_water_heater': '',
        'tariff_high_rate': 0.18,
        'tariff_low_rate': 0.12,
        'update_hour': 7,
        'historical_start_date': '2024-01-01',
        'historical_import': True,
        'force_reimport': False,
        'log_level': 'info',
    }
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
            defaults.update({k: v for k, v in cfg.items() if v != '' and v is not None})
        except Exception as e:
            logger.error(f"Config load error: {e}")
    return defaults


# ─── State persistence ────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {'last_imported_date': None, 'historical_complete': False}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ─── Scheduling ───────────────────────────────────────────────────────────────

async def sleep_until_next_run(target_hour: int, client):
    """Sleep until the next occurrence of target_hour:00, pinging randomly to keep session alive."""
    now = datetime.now()
    next_run = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=1)

    total_wait = (next_run - now).total_seconds()
    logger.info(f"Next data fetch at {next_run.strftime('%Y-%m-%d %H:%M:%S')} (in {total_wait/3600:.1f}h)")

    while True:
        now = datetime.now()
        wait_seconds = (next_run - now).total_seconds()

        if wait_seconds <= 0:
            break

        # Randomize ping interval between 20 and 40 minutes to avoid regular polling patterns
        ping_interval = random.randint(1200, 2400)

        if wait_seconds > ping_interval:
            await asyncio.sleep(ping_interval)
            logger.debug(f"Pinging ZSDIS portal to keep session alive (slept {ping_interval//60}m)...")
            if not await client.is_session_valid():
                logger.info("Session expired during idle — re-authenticating...")
                if not await client.ensure_authenticated():
                    await client.wait_for_bootstrap_and_authenticate()
        else:
            await asyncio.sleep(wait_seconds)
            break


# ─── Core tasks ───────────────────────────────────────────────────────────────

async def fetch_and_publish_day(
    client,
    ha,
    target_date: date,
    config: dict,
    nt_intervals: list,
) -> tuple[bool, Optional[dict]]:
    """Fetch one day's data and push to HA."""
    logger.info(f"Fetching data for {target_date}...")
    data = await client.fetch_day(target_date, nt_intervals)

    if not data:
        logger.warning(f"No data returned for {target_date}")
        return False, None

    vt = data['vt_kwh']
    nt = data['nt_kwh']
    total = data['total_kwh']
    high_rate = float(config['tariff_high_rate'])
    low_rate = float(config['tariff_low_rate'])
    cost = vt * high_rate + nt * low_rate

    logger.info(f"  Total={total:.3f} kWh  VT={vt:.3f}  NT={nt:.3f}  Cost={cost:.2f}€")

    monthly_kwh = None
    try:
        monthly_data = await client.fetch_monthly_consumption()
        if monthly_data:
            # Data is descending — first entry is the most recent month
            monthly_kwh = monthly_data[0].get('billedConsumption')
    except Exception as e:
        logger.warning(f"Could not fetch monthly consumption: {e}")

    ok = await ha.publish_energy_sensors(
        date_str=str(target_date),
        total_kwh=total,
        vt_kwh=vt,
        nt_kwh=nt,
        cost_eur=cost,
        tariff_high_rate=high_rate,
        tariff_low_rate=low_rate,
        monthly_kwh=monthly_kwh,
    )
    last_day_data = {
        'date': str(target_date),
        'total_kwh': total,
        'vt_kwh': vt,
        'nt_kwh': nt,
        'cost': cost,
        'monthly_kwh': monthly_kwh,
        'tariff_high_rate': high_rate,
        'tariff_low_rate': low_rate,
    }
    return ok, last_day_data


async def run_historical_import(
    client,
    ha,
    config: dict,
    state: dict,
    nt_intervals: list,
):
    """Import all missing historical data into HA statistics."""
    if state.get('historical_complete'):
        logger.info("Historical import already completed — skipping")
        return

    start_str = config.get('historical_start_date', '2024-01-01')
    try:
        start_date = date.fromisoformat(start_str)
    except ValueError:
        logger.error(f"Invalid historical_start_date: {start_str}")
        return

    # Resume from where we left off
    last_str = state.get('last_imported_date')
    if last_str:
        try:
            resume_date = date.fromisoformat(last_str) + timedelta(days=1)
            if resume_date > start_date:
                start_date = resume_date
        except ValueError:
            pass

    yesterday = date.today() - timedelta(days=1)
    if start_date > yesterday:
        logger.info("No historical dates to import")
        state['historical_complete'] = True
        save_state(state)
        return

    total_days = (yesterday - start_date).days + 1
    logger.info(f"Historical import: {start_date} → {yesterday} ({total_days} days)")
    if nt_intervals:
        logger.info(f"  Using current NT intervals for VT/NT split: {nt_intervals}")
    else:
        logger.info("  No NT intervals — all historical consumption will be treated as VT")

    # Process in 30-day batches to avoid hammering the portal
    all_records = []
    current = start_date
    batch_num = 0

    while current <= yesterday:
        batch_end = min(current + timedelta(days=29), yesterday)
        batch_num += 1
        logger.info(f"  Batch {batch_num}: {current} → {batch_end}")

        records = await client.fetch_range(current, batch_end, nt_intervals)
        all_records.extend(records)

        if records:
            state['last_imported_date'] = str(batch_end)
            save_state(state)

        current = batch_end + timedelta(days=1)
        if current <= yesterday:
            logger.info("  Pausing 10s between batches...")
            await asyncio.sleep(10)

    if all_records:
        logger.info(f"Importing {len(all_records)} days to HA statistics...")
        ok = await ha.import_historical_statistics(all_records)
        if ok:
            logger.info("Historical import complete")
            state['historical_complete'] = True
            state['last_imported_date'] = str(yesterday)
            save_state(state)
        else:
            logger.error("Historical import had errors — will retry next run")
    else:
        logger.warning("No historical records fetched (portal may have limited history)")
        state['historical_complete'] = True
        save_state(state)


async def update_tariff_sensors(ha, parser, config: dict) -> tuple[dict, list]:
    """Fetch current HDO schedule, sync to HA Schedule Helpers, and publish tariff sensors."""
    status = await parser.get_current_status()
    primary_code = config.get('hdo_primary', '')
    wh_code = config.get('hdo_water_heater', '')

    intervals_primary = parser._schedule_cache.get(primary_code, []) if primary_code else []
    primary_entity_id = None
    if primary_code:
        primary_entity_id = await ha.sync_schedule_helper(
            name="ZSDIS NT Schedule",
            icon="mdi:home-clock-outline",
            intervals=intervals_primary
        )
        if primary_entity_id:
            logger.info(f"Synced primary schedule to helper: {primary_entity_id}")

    intervals_wh = parser._schedule_cache.get(wh_code, []) if wh_code else []
    wh_entity_id = None
    if wh_code:
        wh_entity_id = await ha.sync_schedule_helper(
            name="ZSDIS Water Heater Schedule",
            icon="mdi:water-boiler",
            intervals=intervals_wh
        )
        if wh_entity_id:
            logger.info(f"Synced water heater schedule to helper: {wh_entity_id}")

    sot_intervals = []
    if primary_entity_id:
        sot_intervals = await ha.get_schedule_intervals(primary_entity_id)
    
    if not sot_intervals:
        logger.info("Using freshly scraped intervals as SoT fallback (history unavailable yet).")
        sot_intervals = intervals_primary

    await ha.publish_tariff_sensors(
        current_tariff=status.get('primary_tariff', 'unknown'),
        next_switch=status.get('next_switch', 'unknown'),
        hdo_primary_active=status.get('hdo_primary_active', False),
        primary_code=primary_code,
    )
    logger.info(
        f"  Tariff: {status.get('primary_tariff', 'unknown')}, "
        f"next switch: {status.get('next_switch', 'unknown')} "
        f"(source: {status.get('schedule_source', 'unknown')})"
    )
    return status, sot_intervals


# ─── Main ─────────────────────────────────────────────────────────────────────

async def main():
    logger.info("=" * 60)
    logger.info("ZSDIS Diportal Client starting...")
    logger.info("=" * 60)

    config = load_config()
    state = load_state()

    log_level = config.get('log_level', 'info').upper()
    logging.getLogger().setLevel(getattr(logging, log_level, logging.INFO))

    cookie_string = config.get('cookie_string', '')
    update_hour = int(config.get('update_hour', 7))

    try:
        from zsdis_client import ZSDISClient
        from ha_integration import HomeAssistantIntegration
        from hdo_parser import HDOParser
    except ImportError as e:
        logger.error(f"Failed to import modules: {e}")
        sys.exit(1)

    ha = HomeAssistantIntegration()
    client = ZSDISClient(
        cookie_string=cookie_string,
        twocaptcha_api_key=config.get('twocaptcha_api_key', ''),
        portal_username=config.get('portal_username', ''),
        portal_password=config.get('portal_password', ''),
    )
    parser = HDOParser(
        primary_code=config.get('hdo_primary', ''),
        water_heater_code=config.get('hdo_water_heater', '')
    )

    # ── Force Re-import ──
    if config.get('force_reimport', False):
        logger.warning("FORCE RE-IMPORT enabled — resetting historical state")
        state['historical_complete'] = False
        state['last_imported_date'] = None
        save_state(state)
        await ha.disable_force_reimport()

    # ── Authentication ──
    logger.info("Authenticating with diportal.sk...")
    authenticated = await client.ensure_authenticated()

    if not authenticated:
        logger.warning("Waiting for manual cookie bootstrap...")
        authenticated = await client.wait_for_bootstrap_and_authenticate()
        if not authenticated:
            logger.error("Could not authenticate — exiting")
            sys.exit(1)

    logger.info("Authenticated!")

    # ── Auto-Discover IDs ──
    dp_id = config.get('delivery_point_id', '')
    bp_id = config.get('business_partner_id', '')

    if not dp_id or not bp_id:
        logger.info("IDs not provided in config — attempting auto-discovery...")
        discovered_bp, discovered_dp = await client.auto_discover_ids()
        if discovered_bp and discovered_dp:
            bp_id = discovered_bp
            dp_id = discovered_dp
        else:
            logger.error(
                "Auto-discovery failed. Please provide delivery_point_id and "
                "business_partner_id manually in the Add-on configuration."
            )
            sys.exit(1)
            
    # Set them in os.environ since zsdis_client uses them in fetch_day via os.environ (if it still does)
    os.environ['BUSINESS_PARTNER_ID'] = bp_id
    os.environ['DELIVERY_POINT_ID'] = dp_id

    # ── HDO status & Tariff sensors ──
    status, nt_intervals = await update_tariff_sensors(ha, parser, config)

    # ── Historical import (first run only) ──
    if config.get('historical_import', True) and not state.get('historical_complete'):
        logger.info("Starting historical data import...")
        await run_historical_import(client, ha, config, state, nt_intervals)

    # ── Fetch any missing days immediately on startup ──
    yesterday = date.today() - timedelta(days=1)
    last_imported_str = state.get('last_imported_date')
    last_day_data = state.get('last_day_data')
    
    # Restore sensors immediately if we already have yesterday's data
    if last_day_data and last_day_data.get('date') == str(yesterday):
        logger.info(f"Restoring sensors for {yesterday} from state...")
        await ha.publish_energy_sensors(
            date_str=last_day_data['date'],
            total_kwh=last_day_data['total_kwh'],
            vt_kwh=last_day_data['vt_kwh'],
            nt_kwh=last_day_data['nt_kwh'],
            cost_eur=last_day_data['cost'],
            tariff_high_rate=last_day_data['tariff_high_rate'],
            tariff_low_rate=last_day_data['tariff_low_rate'],
            monthly_kwh=last_day_data.get('monthly_kwh'),
        )
    
    if last_imported_str:
        start_fetch_date = date.fromisoformat(last_imported_str) + timedelta(days=1)
        # If we didn't have last_day_data, we might need to re-fetch yesterday to populate sensors
        if start_fetch_date > yesterday and not last_day_data:
            start_fetch_date = yesterday
    else:
        start_fetch_date = yesterday

    if start_fetch_date <= yesterday:
        current = start_fetch_date
        while current <= yesterday:
            ok, day_data = await fetch_and_publish_day(client, ha, current, config, nt_intervals)
            if ok:
                state['last_imported_date'] = str(current)
                if day_data:
                    state['last_day_data'] = day_data
                save_state(state)
            else:
                break
            current += timedelta(days=1)

    # ── Main loop: run once daily at update_hour ──
    logger.info(f"Init complete. Running daily at {update_hour:02d}:00")

    while True:
        await sleep_until_next_run(update_hour, client)

        logger.info("=" * 40)
        logger.info(f"Daily run — {datetime.now().strftime('%Y-%m-%d %H:%M')}")

        max_retries = 3
        retry_delay_minutes = 15

        for attempt in range(1, max_retries + 1):
            if not await client.is_session_valid():
                logger.info("Session expired — re-authenticating...")
                if not await client.ensure_authenticated():
                    logger.error(f"Re-authentication failed (attempt {attempt}/{max_retries})")
                    if attempt < max_retries:
                        await asyncio.sleep(retry_delay_minutes * 60)
                    continue

            # Refresh HDO intervals and tariff sensors daily
            # Clear the parser cache so it fetches fresh data for the new day
            parser._cache_date = None
            parser._schedule_cache = None
            status, nt_intervals = await update_tariff_sensors(ha, parser, config)

            yesterday = date.today() - timedelta(days=1)
            last_imported_str = state.get('last_imported_date')
            if last_imported_str:
                start_fetch_date = date.fromisoformat(last_imported_str) + timedelta(days=1)
            else:
                start_fetch_date = yesterday

            if start_fetch_date > yesterday:
                logger.info("Daily data is already up to date")
                break

            all_ok = True
            current = start_fetch_date
            while current <= yesterday:
                ok, day_data = await fetch_and_publish_day(client, ha, current, config, nt_intervals)
                if ok:
                    state['last_imported_date'] = str(current)
                    if day_data:
                        state['last_day_data'] = day_data
                    save_state(state)
                else:
                    all_ok = False
                    break
                current += timedelta(days=1)

            if all_ok:
                logger.info("Daily run complete")
                break
            else:
                logger.warning(f"Data fetch failed (attempt {attempt}/{max_retries})")
                if attempt < max_retries:
                    logger.info(f"Retrying in {retry_delay_minutes} minutes...")
                    await asyncio.sleep(retry_delay_minutes * 60)
                else:
                    logger.error("All retries exhausted — will retry tomorrow")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)