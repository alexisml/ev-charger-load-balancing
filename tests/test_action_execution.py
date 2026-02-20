"""Tests for the action execution contract (PR-4).

Tests cover:
- set_current action fires with correct payload when charger current changes
- stop_charging action fires when headroom drops below minimum
- start_charging + set_current fire in order when charging resumes
- No actions fire when no state transition occurs
- No actions fire when action scripts are not configured
- Error handling: a failing action script logs a warning but does not break the integration
- Payload validation: set_current receives current_a as a float
- Fallback-to-stop triggers stop_charging action
- Meter recovery triggers start_charging + set_current when resuming from stop
"""

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.ev_lb.const import (
    CONF_ACTION_SET_CURRENT,
    CONF_ACTION_START_CHARGING,
    CONF_ACTION_STOP_CHARGING,
    CONF_MAX_SERVICE_CURRENT,
    CONF_POWER_METER_ENTITY,
    CONF_VOLTAGE,
    DOMAIN,
)

POWER_METER = "sensor.house_power_w"
SET_CURRENT_SCRIPT = "script.ev_lb_set_current"
STOP_CHARGING_SCRIPT = "script.ev_lb_stop_charging"
START_CHARGING_SCRIPT = "script.ev_lb_start_charging"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in all tests."""
    yield


@pytest.fixture
def mock_config_entry_with_actions() -> MockConfigEntry:
    """Create a mock config entry with all three action scripts configured."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_POWER_METER_ENTITY: POWER_METER,
            CONF_VOLTAGE: 230.0,
            CONF_MAX_SERVICE_CURRENT: 32.0,
            CONF_ACTION_SET_CURRENT: SET_CURRENT_SCRIPT,
            CONF_ACTION_STOP_CHARGING: STOP_CHARGING_SCRIPT,
            CONF_ACTION_START_CHARGING: START_CHARGING_SCRIPT,
        },
        title="EV Load Balancing",
    )


@pytest.fixture
def mock_config_entry_no_actions() -> MockConfigEntry:
    """Create a mock config entry with no action scripts configured."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_POWER_METER_ENTITY: POWER_METER,
            CONF_VOLTAGE: 230.0,
            CONF_MAX_SERVICE_CURRENT: 32.0,
        },
        title="EV Load Balancing",
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up the integration and create the power meter sensor."""
    hass.states.async_set(POWER_METER, "0")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


def _get_entity_id(
    hass: HomeAssistant, entry: MockConfigEntry, platform: str, suffix: str
) -> str:
    """Look up entity_id from the entity registry."""
    ent_reg = er.async_get(hass)
    entity_id = ent_reg.async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{suffix}"
    )
    assert entity_id is not None
    return entity_id


# ---------------------------------------------------------------------------
# set_current action
# ---------------------------------------------------------------------------


class TestSetCurrentAction:
    """Verify set_current action fires when the charger current changes."""

    async def test_set_current_fires_on_initial_charge(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """set_current and start_charging fire when charging starts from zero."""
        calls = async_mock_service(hass, "script", "turn_on")
        await _setup(hass, mock_config_entry_with_actions)

        # 5000 W at 230 V → headroom ≈ 10.3 → target = 10 A (from 0 → resume)
        hass.states.async_set(POWER_METER, "5000")
        await hass.async_block_till_done()

        # Should fire start_charging then set_current (resume transition)
        assert len(calls) == 2
        assert calls[0].data["entity_id"] == START_CHARGING_SCRIPT
        assert calls[1].data["entity_id"] == SET_CURRENT_SCRIPT
        assert calls[1].data["variables"]["current_a"] == 10.0

    async def test_set_current_fires_on_current_adjustment(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """set_current fires when the charger current changes while already active."""
        calls = async_mock_service(hass, "script", "turn_on")
        await _setup(hass, mock_config_entry_with_actions)

        # Step 1: start charging at 18 A (3000 W at 230 V)
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        calls.clear()

        # Step 2: higher load → current drops from 18 A to 15 A (already active)
        hass.states.async_set(POWER_METER, "8000")
        await hass.async_block_till_done()

        # Only set_current should fire (adjust, not resume)
        assert len(calls) == 1
        assert calls[0].data["entity_id"] == SET_CURRENT_SCRIPT
        assert calls[0].data["variables"]["current_a"] == 15.0

    async def test_set_current_payload_contains_current_a_as_float(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """The set_current payload includes current_a as a float value."""
        calls = async_mock_service(hass, "script", "turn_on")
        await _setup(hass, mock_config_entry_with_actions)

        hass.states.async_set(POWER_METER, "5000")
        await hass.async_block_till_done()

        set_current_calls = [
            c for c in calls if c.data["entity_id"] == SET_CURRENT_SCRIPT
        ]
        assert len(set_current_calls) == 1
        current_a = set_current_calls[0].data["variables"]["current_a"]
        assert isinstance(current_a, float)
        assert current_a > 0


# ---------------------------------------------------------------------------
# stop_charging action
# ---------------------------------------------------------------------------


class TestStopChargingAction:
    """Verify stop_charging action fires when charging must stop."""

    async def test_stop_charging_fires_on_overload(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """stop_charging fires when headroom drops below minimum EV current."""
        calls = async_mock_service(hass, "script", "turn_on")
        await _setup(hass, mock_config_entry_with_actions)

        # Step 1: start charging at 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        calls.clear()

        # Step 2: extreme load → 12000 W at 230 V ≈ 52.2 A → available = -20.2
        # raw target = 18 + (-20.2) = -2.2 → below min → stop
        hass.states.async_set(POWER_METER, "12000")
        await hass.async_block_till_done()

        stop_calls = [
            c for c in calls if c.data["entity_id"] == STOP_CHARGING_SCRIPT
        ]
        assert len(stop_calls) == 1
        # stop_charging should not have variables
        assert "variables" not in stop_calls[0].data

    async def test_stop_fires_when_meter_unavailable_in_stop_mode(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """stop_charging fires when meter becomes unavailable (default stop mode)."""
        calls = async_mock_service(hass, "script", "turn_on")
        await _setup(hass, mock_config_entry_with_actions)

        # Start charging
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        calls.clear()

        # Meter goes unavailable → stop mode → stop charging
        hass.states.async_set(POWER_METER, "unavailable")
        await hass.async_block_till_done()

        stop_calls = [
            c for c in calls if c.data["entity_id"] == STOP_CHARGING_SCRIPT
        ]
        assert len(stop_calls) == 1


# ---------------------------------------------------------------------------
# start_charging + set_current (resume)
# ---------------------------------------------------------------------------


class TestResumeChargingActions:
    """Verify start_charging and set_current fire in order when charging resumes."""

    async def test_resume_fires_start_then_set_current(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """When resuming from stopped, start_charging fires before set_current."""
        calls = async_mock_service(hass, "script", "turn_on")
        await _setup(hass, mock_config_entry_with_actions)
        coordinator = hass.data[DOMAIN][mock_config_entry_with_actions.entry_id][
            "coordinator"
        ]

        # Use a controllable clock to handle ramp-up cooldown
        mock_time = 1000.0

        def fake_monotonic():
            return mock_time

        coordinator._time_fn = fake_monotonic

        # Step 1: start charging at 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        # Step 2: extreme overload → stop (12000 W, raw target < 0)
        mock_time = 1001.0
        hass.states.async_set(POWER_METER, "12000")
        await hass.async_block_till_done()

        calls.clear()

        # Step 3: load drops and cooldown has elapsed → resume
        mock_time = 1032.0  # 31 s after reduction (> 30 s cooldown)
        hass.states.async_set(POWER_METER, "5000")
        await hass.async_block_till_done()

        # Should fire start_charging then set_current (resume transition)
        assert len(calls) == 2
        assert calls[0].data["entity_id"] == START_CHARGING_SCRIPT
        assert calls[1].data["entity_id"] == SET_CURRENT_SCRIPT
        assert calls[1].data["variables"]["current_a"] > 0


# ---------------------------------------------------------------------------
# No actions fire when there is no state transition
# ---------------------------------------------------------------------------


class TestNoActionOnNoChange:
    """Verify no actions fire when state does not change."""

    async def test_no_action_when_current_unchanged(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """No action fires when the computed current stays the same."""
        calls = async_mock_service(hass, "script", "turn_on")
        await _setup(hass, mock_config_entry_with_actions)

        # Step 1: start charging at 18 A (3000 W)
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        calls.clear()

        # Step 2: same power meter value → same target → no action
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        assert len(calls) == 0

    async def test_no_action_when_already_stopped(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """No action fires when the charger is already stopped and stays stopped."""
        calls = async_mock_service(hass, "script", "turn_on")
        await _setup(hass, mock_config_entry_with_actions)

        # Step 1: overload from the start → charger is stopped
        hass.states.async_set(POWER_METER, "9000")
        await hass.async_block_till_done()

        calls.clear()

        # Step 2: still overloaded → charger remains stopped → no action
        hass.states.async_set(POWER_METER, "9500")
        await hass.async_block_till_done()

        assert len(calls) == 0


# ---------------------------------------------------------------------------
# No actions when scripts are not configured
# ---------------------------------------------------------------------------


class TestNoActionsConfigured:
    """Verify the integration works without action scripts (backward compatibility)."""

    async def test_no_actions_called_when_not_configured(
        self,
        hass: HomeAssistant,
        mock_config_entry_no_actions: MockConfigEntry,
    ) -> None:
        """No service calls are made when no action scripts are configured."""
        calls = async_mock_service(hass, "script", "turn_on")
        await _setup(hass, mock_config_entry_no_actions)

        # Normal operation — should compute target but make no service calls
        hass.states.async_set(POWER_METER, "5000")
        await hass.async_block_till_done()

        current_set_id = _get_entity_id(
            hass, mock_config_entry_no_actions, "sensor", "current_set"
        )
        assert float(hass.states.get(current_set_id).state) == 10.0
        assert len(calls) == 0


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestActionErrorHandling:
    """Verify the integration handles action script failures gracefully."""

    async def test_failed_action_logs_warning_but_continues(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A failing action script logs a warning but does not crash the integration."""
        # Do NOT mock the script service — the call will raise ServiceNotFound
        await _setup(hass, mock_config_entry_with_actions)

        # Trigger a state change that would call start_charging + set_current
        hass.states.async_set(POWER_METER, "5000")
        await hass.async_block_till_done()

        # Integration should still be operational — the sensor updates
        current_set_id = _get_entity_id(
            hass, mock_config_entry_with_actions, "sensor", "current_set"
        )
        assert float(hass.states.get(current_set_id).state) == 10.0

        # Warning should be logged about failed action
        assert "failed" in caplog.text.lower() or "Action" in caplog.text
