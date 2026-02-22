"""Integration tests for setup/unload lifecycle and config flow changes.

Tests exercise the full integration lifecycle from config entry setup
through normal operation to unload, as well as options flow updates
that modify behavior during active operation.
"""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.ev_lb.const import (
    CONF_ACTION_SET_CURRENT,
    CONF_ACTION_START_CHARGING,
    CONF_ACTION_STOP_CHARGING,
    DOMAIN,
    SERVICE_SET_LIMIT,
)
from conftest import (
    POWER_METER,
    SET_CURRENT_SCRIPT,
    STOP_CHARGING_SCRIPT,
    START_CHARGING_SCRIPT,
    setup_integration,
    get_entity_id,
)


# ---------------------------------------------------------------------------
# Scenario 6: Full lifecycle from config setup through to unload
# ---------------------------------------------------------------------------


class TestFullLifecycleSetupToUnload:
    """Complete integration lifecycle: setup → operation → unload → verify cleanup.

    Verifies that services are registered/unregistered, hass.data is populated
    and cleaned up, and all entity platforms load and unload properly.
    """

    async def test_setup_operate_and_unload(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Integration sets up correctly, operates normally, and cleans up fully on unload."""
        await setup_integration(hass, mock_config_entry)

        entry_id = mock_config_entry.entry_id

        # Verify setup: entry loaded, service available, data populated
        assert mock_config_entry.state is ConfigEntryState.LOADED
        assert hass.services.has_service(DOMAIN, SERVICE_SET_LIMIT)
        assert DOMAIN in hass.data
        assert entry_id in hass.data[DOMAIN]
        assert "coordinator" in hass.data[DOMAIN][entry_id]

        # Verify all entity platforms loaded
        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")
        available_id = get_entity_id(hass, mock_config_entry, "sensor", "available_current")
        active_id = get_entity_id(hass, mock_config_entry, "binary_sensor", "active")
        meter_id = get_entity_id(hass, mock_config_entry, "binary_sensor", "meter_status")
        max_id = get_entity_id(hass, mock_config_entry, "number", "max_charger_current")
        switch_id = get_entity_id(hass, mock_config_entry, "switch", "enabled")
        assert all(
            hass.states.get(eid) is not None
            for eid in [current_set_id, available_id, active_id, meter_id, max_id, switch_id]
        )

        # Operate: set a meter value and verify state updates
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 18.0

        # Use set_limit service to verify it works
        await hass.services.async_call(
            DOMAIN, SERVICE_SET_LIMIT, {"current_a": 16.0}, blocking=True
        )
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 16.0

        # Unload
        await hass.config_entries.async_unload(entry_id)
        await hass.async_block_till_done()

        # Verify cleanup
        assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
        assert not hass.services.has_service(DOMAIN, SERVICE_SET_LIMIT)
        assert entry_id not in hass.data.get(DOMAIN, {})


# ---------------------------------------------------------------------------
# Scenario 9: Options flow update during operation
# ---------------------------------------------------------------------------


class TestOptionsFlowDuringOperation:
    """User adds action scripts via options flow after initial setup without actions.

    Verifies that the updated action configuration takes effect for
    subsequent state transitions.
    """

    async def test_add_actions_via_options_then_verify_firing(
        self,
        hass: HomeAssistant,
        mock_config_entry_no_actions: MockConfigEntry,
    ) -> None:
        """Adding action scripts via options flow makes them fire on the next state transition."""
        calls = async_mock_service(hass, "script", "turn_on")
        await setup_integration(hass, mock_config_entry_no_actions)

        current_set_id = get_entity_id(hass, mock_config_entry_no_actions, "sensor", "current_set")

        # Phase 1: Charge without actions → no script calls
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 18.0
        assert len(calls) == 0

        # Phase 2: Add action scripts via options flow
        result = await hass.config_entries.options.async_init(
            mock_config_entry_no_actions.entry_id,
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_ACTION_SET_CURRENT: SET_CURRENT_SCRIPT,
                CONF_ACTION_STOP_CHARGING: STOP_CHARGING_SCRIPT,
                CONF_ACTION_START_CHARGING: START_CHARGING_SCRIPT,
            },
        )
        assert result["type"] == "create_entry"

        calls.clear()

        # Phase 3: Next meter event should now fire actions
        # Change meter to trigger a state transition
        hass.states.async_set(POWER_METER, "5000")
        await hass.async_block_till_done()

        new_current = float(hass.states.get(current_set_id).state)
        assert new_current > 0

        # Actions should now fire since we added them via options
        set_calls = [c for c in calls if c.data["entity_id"] == SET_CURRENT_SCRIPT]
        assert len(set_calls) >= 1
