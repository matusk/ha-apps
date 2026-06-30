# ZSDIS Diportal Client (Home Assistant App)

This Home Assistant App automatically fetches your daily energy consumption and HDO tariff schedule from [ZSDIS diportal.sk](https://www.diportal.sk) and pushes them into your Home Assistant Energy Dashboard.

It tracks:
- **Daily total consumption** (backfilled directly into HA Statistics)
- **VT/NT split** — high and low tariff consumption derived from your HDO schedule
- **Monthly aggregated consumption** (for recent billing periods)
- **HDO Tariff Status** — know exactly when your high/low tariffs or water heater circuit switch

## Installation

1. In your Home Assistant UI, navigate to **Settings → Add-ons → Add-on Store**.
2. Click the three dots (top right) and select **Repositories**.
3. Add the URL of this repository: `https://github.com/matusk/ha-apps`
4. Refresh the page and search for **ZSDIS Diportal Client**.
5. Click **Install**.

## Authentication & Configuration

The ZSDIS portal (diportal.sk) is protected by Google invisible reCAPTCHA, which historically required manual cookie extraction. **This app now supports two modes of authentication**: Fully Automated via 2Captcha (Recommended), or Manual via Session Cookies.

You can configure *either* or *both*. If both are configured, the app will always prioritize existing valid cookies, and if they expire, it will automatically fall back to the 2Captcha solver to get fresh cookies.

### Option A: Fully Automated via 2Captcha (Recommended)

Since the portal requires solving a CAPTCHA to log in, you can provide an API key for [2Captcha.com](https://2captcha.com/) (costs about $2.99 for 1000 automated logins). When the session expires, the app will automatically submit the captcha, wait for the solution, log in, and save the fresh session cookies for you!

1. Go to the App **Configuration** tab.
2. Fill in your `twocaptcha_api_key`.
3. Fill in your `portal_username` (e.g., `name.surname@domain.sk`).
4. Fill in your `portal_password`.
5. Leave `cookie_string` blank (unless you also want to provide a manual fallback).
6. **Save** and **Start** the app.

### Option B: Manual Cookie Extraction (Fallback)

If you do not want to use 2Captcha, you can manually extract your browser's session cookie. Note that diportal cookies usually expire within 24 hours, so you will need to repeat this process frequently if you don't use 2Captcha.

1. Open Google Chrome on your computer.
2. Go to [https://www.diportal.sk](https://www.diportal.sk) and **Log In**.
3. Press `F12` to open Developer Tools, and navigate to the **Network** tab.
4. Refresh the page (`F5`).
5. Click on any request starting with `www.diportal.sk` in the Network list.
6. Scroll down to the **Request Headers** section and find the **`Cookie:`** header.
7. Right-click the `Cookie:` value and select **Copy Value**.
8. Paste this text into the `cookie_string` field in the App Configuration.

### App Configuration Reference

Go to the **Configuration** tab of the App:

| Option | Default | Description |
|--------|---------|-------------|
| `twocaptcha_api_key` | *(blank)* | Your API key for 2Captcha.com (for automated login) |
| `portal_username` | *(blank)* | Your login email for diportal.sk |
| `portal_password` | *(blank)* | Your password for diportal.sk |
| `cookie_string` | *(blank)* | Your raw Cookie header string from diportal.sk (for manual login) |
| `hdo_primary` | `259` | Your main tariff HDO code from your ZSDIS contract |
| `hdo_water_heater` | `246` | Your boiler/water heater HDO code |
| `tariff_high_rate` | `0.18` | High tariff price in €/kWh |
| `tariff_low_rate` | `0.12` | Low tariff price in €/kWh |
| `historical_import` | `true` | Download history into HA Statistics on first start |
| `historical_start_date` | `2024-01-01` | Start date for backfill (`YYYY-MM-DD`) |
| `force_reimport` | `false` | Toggle to `true` to wipe and re-download all historical data |
| `keep_alive_ping` | `false` | Ping the portal every 20–40 min to prevent session expiry during idle. Only needed if you use **manual cookies without 2Captcha** — when 2Captcha is configured, the session is renewed automatically on the next daily run. |
| `update_hour` | `7` | Hour of day (0–23) when daily data is fetched |
| `log_level` | `info` | Log verbosity |

Click **Save** and **Start** the App. Check the logs — you should see the App automatically discovering your Delivery Point.

### VT/NT Split

The portal API does not report VT (high tariff) and NT (low tariff) consumption separately. The App derives the split by cross-referencing each 15-minute interval's timestamp against the HDO schedule fetched from the public ZSDIS website. Configure `hdo_primary` correctly for accurate VT/NT figures.

### Historical Data & Force Re-Import

On first startup, the App downloads all missing historical energy data from `historical_start_date` up to yesterday and pushes it directly into the HA Energy Dashboard Statistics. A `state.json` file is written so this is never repeated.

#### Safe Testing / First Run
If you want to test the App without potentially corrupting your Energy Dashboard with years of historical data:
1. In the **Configuration** tab, toggle **`historical_import`** to **`false`**.
2. Click **Save** and **Start** the App.
3. Check the Logs to ensure authentication succeeds and that the schedule helpers (`ZSDIS NT Schedule`) are created correctly.
4. Check Developer Tools -> States for `sensor.zsdis_yesterday_total` to verify the parsed data.
5. Once you are confident it works, toggle **`historical_import`** back to **`true`** and restart the App to safely backfill your historical statistics.

To re-download all historical data from scratch:
1. Go to the App **Configuration** tab.
2. Toggle **Force Re-import** to `true`.
3. Restart the App.
4. The App resets its tracking state, queues the full download, and automatically toggles the option back to `false`.

## Home Assistant Energy Dashboard Setup

To set this data as your main power consumption in Home Assistant, you need to add the sensors to the official Energy Dashboard:

1. Go to **Settings → Dashboards → Energy**.
2. Under the **Electricity grid** section, click **Add Consumption**.
3. **Choose your setup:**
   - **Option A (Simple Total):** Select the `sensor.zsdis_yesterday_total` entity.
   - **Option B (Multi-Tariff):** If you want Home Assistant to natively understand your High and Low tariffs:
     1. Add `sensor.zsdis_yesterday_vt` and enter your High tariff price.
     2. Click "Add Consumption" again, select `sensor.zsdis_yesterday_nt`, and enter your Low tariff price.
4. Click **Save**.

*Note: Because ZSDIS only publishes data the following morning, your Energy Dashboard for "Today" will usually be empty, but your dashboard for "Yesterday" and all previous historical days will be perfectly populated!*

## Troubleshooting

- **Session Expired:** If using manual cookies without 2Captcha, repeat the DevTools cookie extraction process, paste the new string into the App Configuration, and restart the App. You can also enable `keep_alive_ping: true` to have the App ping the portal every 20–40 minutes to prevent the session from expiring — but this is unnecessary if 2Captcha is configured, since the session will be renewed automatically at the next daily run.
- **No data returned for date:** Check `/data/raw/raw_response_YYYY-MM-DD.json` inside the App's data directory to inspect the raw API response.
- **VT/NT both zero or wrong split:** Verify your `hdo_primary` code matches your ZSDIS contract. The App fetches the live HDO schedule from `www.zsdis.sk`.

## Privacy & Security

This App connects directly from your Home Assistant instance to the ZSDIS servers (and optionally to 2Captcha for automated logins). Your portal data never leaves your local network.