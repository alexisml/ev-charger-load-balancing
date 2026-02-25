"""Tests for the overload correction loop.

When available current drops below zero (consumption exceeds the service
limit), the coordinator schedules a trigger after *overload_trigger_delay_s*
seconds and then fires a periodic loop every *overload_loop_interval_s*
seconds until the overload clears.  This ensures the charger is corrected
even if the power meter does not report a new state value.

Covers:
- No timers are created when available current is positive
- A trigger timer is scheduled when the system first becomes overloaded
- After the trigger delay the correction loop starts while still overloaded
- All timers are cancelled once available current returns to zero or above
- Timers are cleaned up when the coordinator is stopped
"""

from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ev_lb.const import DOMAIN
from conftest import POWER_METER, setup_integration


class TestOverloadCorrectionLoop:
    """The coordinator triggers a rapid correction loop when the system is overloaded.

    When available current drops below zero (consumption exceeds the service
    limit) the coordinator schedules a trigger after *overload_trigger_delay_s*
    seconds and then fires a periodic loop every *overload_loop_interval_s*
    seconds until the overload clears.  This ensures the charger is corrected
    even if the power meter does not report a new state value.
    """

    async def test_overload_loop_not_started_without_overload(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """No overload timers are created when available current is positive."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]

        # 3 kW → available > 0
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        assert coordinator._overload_trigger_unsub is None
        assert coordinator._overload_loop_unsub is None

    async def test_overload_trigger_scheduled_when_overloaded(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """A trigger timer is scheduled when the system first becomes overloaded."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator.overload_trigger_delay_s = 2.0

        # Set current so that non-EV load is 0; push power far above service limit
        # service_current = 9000/230 ≈ 39.1 A > 32 A service limit → overloaded
        hass.states.async_set(POWER_METER, "9000")
        await hass.async_block_till_done()

        assert coordinator.available_current_a < 0
        assert coordinator._overload_trigger_unsub is not None
        assert coordinator._overload_loop_unsub is None  # loop not yet started

    async def test_overload_trigger_fires_and_starts_loop(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """After the trigger delay the correction loop starts while still overloaded."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator.overload_trigger_delay_s = 2.0
        coordinator.overload_loop_interval_s = 5.0

        # Drive the system into overload
        hass.states.async_set(POWER_METER, "9000")
        await hass.async_block_till_done()
        assert coordinator._overload_trigger_unsub is not None

        # Cancel the real timer and fire the callback directly to avoid lingering timer
        coordinator._overload_trigger_unsub()
        coordinator._overload_trigger_unsub = None
        import homeassistant.util.dt as ha_dt
        coordinator._on_overload_triggered(ha_dt.utcnow())
        await hass.async_block_till_done()

        # Loop should be running since still overloaded
        assert coordinator._overload_loop_unsub is not None

    async def test_overload_timers_cancelled_when_cleared(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """All overload timers are cancelled once available current returns to zero or above."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator.overload_trigger_delay_s = 2.0

        # Drive into overload
        hass.states.async_set(POWER_METER, "9000")
        await hass.async_block_till_done()
        assert coordinator._overload_trigger_unsub is not None

        # Reduce load — now available > 0
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        assert coordinator.available_current_a > 0
        assert coordinator._overload_trigger_unsub is None
        assert coordinator._overload_loop_unsub is None

    async def test_overload_timers_cancelled_on_stop(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Overload timers are cleaned up when the coordinator is stopped."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator.overload_trigger_delay_s = 2.0

        # Drive into overload
        hass.states.async_set(POWER_METER, "9000")
        await hass.async_block_till_done()
        assert coordinator._overload_trigger_unsub is not None

        # Unload the integration
        await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert coordinator._overload_trigger_unsub is None
        assert coordinator._overload_loop_unsub is None
