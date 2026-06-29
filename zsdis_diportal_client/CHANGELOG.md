# Changelog

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
- Session keep-alive pings during idle periods (randomised 20–40 min intervals)
- Force re-import toggle with auto-reset after use
- Slovak translation (`translations/sk.yaml`)

### Removed
- ChromeDriver / undetected-chromedriver dependency
- 2captcha integration
- Utility meter helpers (replaced by direct HA statistics import)
- Smart notifications
- armhf and i386 architecture support

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