#!/usr/bin/with-contenv bashio

# Set bashio log level first — bashio defaults to DEBUG, so all subsequent
# bashio::config calls would emit verbose API debug lines without this.
LOG_LEVEL=$(bashio::config 'log_level' || echo 'info')
bashio::log.level "${LOG_LEVEL}"

# Read remaining config values
HDO_PRIMARY=$(bashio::config 'hdo_primary' || echo '')
HDO_WATER_HEATER=$(bashio::config 'hdo_water_heater' || echo '')
UPDATE_HOUR=$(bashio::config 'update_hour' || echo '7')

bashio::log.info "Starting ZSDIS Diportal Client v$(bashio::addon.version)..."
bashio::log.info "HDO primary: ${HDO_PRIMARY}, water heater: ${HDO_WATER_HEATER}"
bashio::log.info "Daily update at: ${UPDATE_HOUR}:00"

# Export environment variables for Python
export HDO_PRIMARY="${HDO_PRIMARY}"
export HDO_WATER_HEATER="${HDO_WATER_HEATER}"
export UPDATE_HOUR="${UPDATE_HOUR}"
export LOG_LEVEL="${LOG_LEVEL}"
export SUPERVISOR_TOKEN="${SUPERVISOR_TOKEN}"
export CONFIG_PATH="/data/options.json"

cd /app && python3 main.py