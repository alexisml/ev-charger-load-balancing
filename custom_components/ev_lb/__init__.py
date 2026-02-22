"""EV Charger Load Balancing integration for Home Assistant."""

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback

from .const import DOMAIN, PLATFORMS, SERVICE_SET_LIMIT
from .coordinator import EvLoadBalancerCoordinator
from ._log import get_logger

_LOGGER = get_logger(__name__)

SERVICE_SET_LIMIT_SCHEMA = vol.Schema(
    {
        vol.Required("current_a"): vol.All(vol.Coerce(float), vol.Range(min=0)),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EV Charger Load Balancing from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    coordinator = EvLoadBalancerCoordinator(hass, entry)

    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    coordinator.async_start()

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    _register_services(hass)

    _LOGGER.debug("Entry %s set up successfully", entry.entry_id)

    return True


@callback
def _register_services(hass: HomeAssistant) -> None:
    """Register the ev_lb.set_limit service (once per domain)."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_LIMIT):
        return

    @callback
    def handle_set_limit(call: ServiceCall) -> None:
        """Handle ev_lb.set_limit service call.

        Applies the requested current to every loaded coordinator.
        In the current single-charger architecture there is exactly one.
        """
        current_a = call.data["current_a"]
        _LOGGER.debug(
            "Service %s.%s called with current_a=%.1f",
            DOMAIN,
            SERVICE_SET_LIMIT,
            current_a,
        )
        for entry_data in hass.data[DOMAIN].values():
            coordinator: EvLoadBalancerCoordinator = entry_data["coordinator"]
            coordinator.manual_set_limit(current_a)

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_LIMIT,
        handle_set_limit,
        schema=SERVICE_SET_LIMIT_SCHEMA,
    )


async def _async_options_updated(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Reload the integration when options are changed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    entry_data = hass.data[DOMAIN].get(entry.entry_id)
    if entry_data:
        entry_data["coordinator"].async_stop()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    # Unregister services when no entries remain
    if not hass.data[DOMAIN]:
        hass.services.async_remove(DOMAIN, SERVICE_SET_LIMIT)

    _LOGGER.debug("Entry %s unloaded (ok=%s)", entry.entry_id, unload_ok)

    return unload_ok
