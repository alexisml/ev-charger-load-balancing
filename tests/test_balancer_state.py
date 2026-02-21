"""Tests for the balancer state diagnostic sensor.

The balancer state sensor shows the operational state of the coordinator,
mapping to the charger state transitions in the README diagrams.

Tests cover:
- Sensor starts in 'stopped' state
- Transitions to 'adjusting' on first power meter event with headroom
- Shows 'active' when target current > 0 and unchanged (steady state)
- Shows 'adjusting' when current changes
- Shows 'ramp_up_hold' when cooldown blocks an increase
- Shows 'meter_unavailable_stopped' when meter goes unavailable in stop mode
- Shows 'meter_unavailable_fallback' when meter goes unavailable in set_current mode
- Shows 'meter_unavailable_ignored' when meter goes unavailable in ignore mode
- Shows 'disabled' when load balancing is turned off
- Shows 'stopped' when overload stops charging
"""

from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ev_lb.const import (
    DOMAIN,
    STATE_ACTIVE,
    STATE_ADJUSTING,
    STATE_DISABLED,
    STATE_METER_UNAVAILABLE_FALLBACK,
    STATE_METER_UNAVAILABLE_IGNORED,
    STATE_METER_UNAVAILABLE_STOPPED,
    STATE_RAMP_UP_HOLD,
    STATE_STOPPED,
)
from conftest import (
    POWER_METER,
    setup_integration,
    get_entity_id,
)


class TestBalancerStateSensor:
    """The balancer state sensor reflects the coordinator's operational state."""

    async def test_initial_state_is_stopped(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Sensor shows 'stopped' before any power meter events."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        assert coordinator.balancer_state == STATE_STOPPED

    async def test_transitions_to_adjusting_on_first_charge(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """When charging starts for the first time, state is 'adjusting' (current changed)."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]

        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        assert coordinator.balancer_state == STATE_ADJUSTING

    async def test_steady_state_is_active(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """When target current > 0 and unchanged, state is 'active'."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]

        # First event — starts charging at 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        # Second event — adjusts to 32 A (max) (different value triggers state change)
        hass.states.async_set(POWER_METER, "3001")
        await hass.async_block_till_done()

        # Third event — same current stays at 32 A (steady state)
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        assert coordinator.balancer_state == STATE_ACTIVE

    async def test_adjusting_on_current_change(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """When current changes while active, state is 'adjusting'."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]

        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        # Increase load — triggers instant reduction
        hass.states.async_set(POWER_METER, "5000")
        await hass.async_block_till_done()

        assert coordinator.balancer_state == STATE_ADJUSTING

    async def test_ramp_up_hold_during_cooldown(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """When cooldown blocks an increase, state is 'ramp_up_hold'."""
        await setup_integration(hass, mock_config_entry)

        # Start charging
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        # Cause a reduction
        hass.states.async_set(POWER_METER, "7500")
        await hass.async_block_till_done()

        # Try to increase — still within cooldown
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        assert coordinator.balancer_state == STATE_RAMP_UP_HOLD

    async def test_stopped_on_overload(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """When overload stops charging, state is 'stopped'."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]

        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        # Heavy overload stops charging
        hass.states.async_set(POWER_METER, "11000")
        await hass.async_block_till_done()

        assert coordinator.balancer_state == STATE_STOPPED

    async def test_meter_unavailable_stop_mode(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """When power meter goes unavailable in stop mode, state is 'meter_unavailable_stopped'."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]

        hass.states.async_set(POWER_METER, "unavailable")
        await hass.async_block_till_done()

        assert coordinator.balancer_state == STATE_METER_UNAVAILABLE_STOPPED

    async def test_meter_unavailable_fallback_mode(
        self, hass: HomeAssistant, mock_config_entry_fallback: MockConfigEntry
    ) -> None:
        """When power meter goes unavailable in set_current mode, state is 'meter_unavailable_fallback'."""
        await setup_integration(hass, mock_config_entry_fallback)
        coordinator = hass.data[DOMAIN][mock_config_entry_fallback.entry_id]["coordinator"]

        hass.states.async_set(POWER_METER, "unavailable")
        await hass.async_block_till_done()

        assert coordinator.balancer_state == STATE_METER_UNAVAILABLE_FALLBACK

    async def test_meter_unavailable_ignore_mode(
        self, hass: HomeAssistant, mock_config_entry_ignore: MockConfigEntry
    ) -> None:
        """When power meter goes unavailable in ignore mode, state is 'meter_unavailable_ignored'."""
        await setup_integration(hass, mock_config_entry_ignore)
        coordinator = hass.data[DOMAIN][mock_config_entry_ignore.entry_id]["coordinator"]

        hass.states.async_set(POWER_METER, "unavailable")
        await hass.async_block_till_done()

        assert coordinator.balancer_state == STATE_METER_UNAVAILABLE_IGNORED

    async def test_disabled_state(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """When load balancing is disabled, state is 'disabled'."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator.enabled = False

        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        assert coordinator.balancer_state == STATE_DISABLED

    async def test_sensor_entity_reflects_coordinator_state(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """The HA sensor entity value matches the coordinator's balancer_state."""
        await setup_integration(hass, mock_config_entry)
        entity_id = get_entity_id(hass, mock_config_entry, "sensor", "balancer_state")

        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_ADJUSTING
