"""Constants for the Oakville Hydro integration."""

DOMAIN = "oakville_hydro"

CONF_ACCOUNT_ID = "account_id"
CONF_SERVICE_ID = "service_id"
CONF_METER = "meter"
CONF_SERVICE_START = "service_start"
# Option: how a water billing period's volume is placed in time.
CONF_WATER_DISTRIBUTION = "water_distribution"
# Entry data: the distribution the stored water statistics were written with.
CONF_WATER_STORED_AS = "water_stored_as"

CONSUMPTION_STATISTIC_ID = f"{DOMAIN}:electricity_consumption"
COST_STATISTIC_ID = f"{DOMAIN}:electricity_cost"
WATER_CONSUMPTION_STATISTIC_ID = f"{DOMAIN}:water_consumption"
WATER_COST_STATISTIC_ID = f"{DOMAIN}:water_cost"
