# Changelog

## [0.0.7] - 2026-07-01

### Fixed
- `Optional` import missing from `main.py` — caused crash on startup
- Bashio log level is now applied before reading other config values, eliminating verbose `DEBUG: Requested API resource` noise in logs at `info` level
- Energy Dashboard statistics now update correctly after every daily fetch — `fetch_and_publish_day` calls `import_day_statistics`, which reads the current cumulative sum from HA before appending, preventing running total from resetting to zero

### Added
- `keep_alive_ping` config option (default `false`) — when enabled, pings the portal every 20–40 min to prevent session expiry; unnecessary when 2Captcha is configured since the session is renewed automatically on each daily run

---

## [0.0.6] - 2026-06-29

### Fixed
- Session expiry handling during idle periods (re-authentication via 2Captcha or cookie bootstrap)
- Incremental daily statistics import correctly continues cumulative sum

---

## [0.0.1] - 2026-06-28

### Changed
- Replaced file-based cookie export with a direct UI configuration input (`cookie_string`), drastically simplifying authentication.
- Delivery Point ID and Business Partner ID are now auto-discovered from the portal API
- Historical data import uses HA `/api/recorder/statistics_import` directly
- Reduced supported architectures to amd64, aarch64, armv7

### Added
- Automatic ID discovery (`auto_discover_ids`) — no need to look up IDs manually
- VT/NT consumption split using the HDO schedule (15-min interval classification)
- Automatic synchronization of scraped HDO schedules to Home Assistant Schedule Helpers (Source of Truth)
- Force re-import toggle with auto-reset after use
- Slovak translation (`translations/sk.yaml`)
- Automated login via 2Captcha (solves Google invisible reCAPTCHA)

---

## [0.0.0] - 2025-10-13

### Added
- Initial release
- ZSDIS diportal.sk data extraction via ChromeDriver
- HDO tariff monitoring and switching detection
- Home Assistant Energy Dashboard integration
- Cost calculation with configurable tariff rates
- Historical data import from configurable start date
- Multi-architecture support (amd64, aarch64, armv7, armhf, i386)
- English and Slovak translations