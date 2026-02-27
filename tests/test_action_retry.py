"""Tests for the action retry/backoff logic and diagnostic sensors.

Tests cover:
- Retry with exponential backoff: failed actions are retried up to ACTION_MAX_RETRIES times
- Backoff timing: retry delays follow exponential pattern (1s, 2s, 4s)
- Successful retry: action succeeds on a later attempt after initial failure
- All retries exhausted: event fired, persistent notification created, error recorded
- Diagnostic state: last_action_error records the failure reason after retries exhausted
- Diagnostic state: last_action_timestamp records the ISO timestamp of each action
- Success clears error: last_action_error is cleared after a successful action
- Notification dismissed on success: action-failed notification is dismissed on recovery
- Diagnostic sensors: last_action_error and last_action_timestamp sensors reflect coordinator state
"""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.ev_lb.const import (
    ACTION_MAX_RETRIES,
    DOMAIN,
    EVENT_ACTION_FAILED,
    NOTIFICATION_ACTION_FAILED_FMT,
)
from conftest import (
    POWER_METER,
    SET_CURRENT_SCRIPT,
    setup_integration,
    collect_events,
    get_entity_id,
    PN_CREATE,
    PN_DISMISS,
)


def _no_sleep_coordinator(hass, entry):
    """Return the coordinator with sleep replaced by a no-op for fast tests."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    coordinator._sleep_fn = AsyncMock()
    return coordinator


# ---------------------------------------------------------------------------
# Retry with exponential backoff
# ---------------------------------------------------------------------------


class TestRetryBackoff:
    """Failed charger actions are retried with exponential backoff before giving up."""

    async def test_retries_exhausted_fires_event_and_records_error(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """Error is recorded and event fires only after all retry attempts are exhausted."""
        await setup_integration(hass, mock_config_entry_with_actions)
        coordinator = _no_sleep_coordinator(hass, mock_config_entry_with_actions)
        events = collect_events(hass, EVENT_ACTION_FAILED)

        with patch(
            "homeassistant.core.ServiceRegistry.async_call",
            side_effect=HomeAssistantError("Script not found"),
        ):
            hass.states.async_set(POWER_METER, "3000")
            await hass.async_block_till_done()

        # Events should fire (one per action that failed after retries)
        assert len(events) >= 1
        assert "Script not found" in events[0]["error"]

        # Diagnostic state should reflect the failure
        assert coordinator.last_action_error is not None
        assert "Script not found" in coordinator.last_action_error
        assert coordinator.last_action_timestamp is not None

    async def test_retry_backoff_delays_are_exponential(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """Retry delays follow an exponential pattern (1s, 2s, 4s)."""
        await setup_integration(hass, mock_config_entry_with_actions)
        coordinator = _no_sleep_coordinator(hass, mock_config_entry_with_actions)

        with patch(
            "homeassistant.core.ServiceRegistry.async_call",
            side_effect=HomeAssistantError("Script not found"),
        ):
            hass.states.async_set(POWER_METER, "3000")
            await hass.async_block_till_done()

        # Verify sleep was called with exponential delays
        sleep_calls = [c.args[0] for c in coordinator._sleep_fn.call_args_list]
        # Each failing action produces retries: delays 1.0, 2.0, 4.0
        # start_charging fails (3 retries) then set_current fails (3 retries)
        expected_pattern = [1.0, 2.0, 4.0]
        # Each failed action should produce the same backoff pattern
        for i in range(0, len(sleep_calls), ACTION_MAX_RETRIES):
            chunk = sleep_calls[i : i + ACTION_MAX_RETRIES]
            assert chunk == expected_pattern

    async def test_successful_retry_after_initial_failure(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """Action succeeds on a later attempt when the first call fails transiently."""
        await setup_integration(hass, mock_config_entry_with_actions)
        coordinator = _no_sleep_coordinator(hass, mock_config_entry_with_actions)
        events = collect_events(hass, EVENT_ACTION_FAILED)

        call_count = 0

        async def flaky_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # Fail on first call of each action, succeed on second
            if call_count % 2 == 1:
                raise HomeAssistantError("Transient error")

        with patch(
            "homeassistant.core.ServiceRegistry.async_call",
            side_effect=flaky_call,
        ):
            hass.states.async_set(POWER_METER, "3000")
            await hass.async_block_till_done()

        # No failure events should fire since retries succeeded
        assert len(events) == 0

        # Diagnostic state should show success
        assert coordinator.last_action_error is None
        assert coordinator.last_action_timestamp is not None

    async def test_total_attempts_equals_one_plus_max_retries(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """Each action is attempted exactly 1 + ACTION_MAX_RETRIES times before giving up."""
        await setup_integration(hass, mock_config_entry_with_actions)
        coordinator = _no_sleep_coordinator(hass, mock_config_entry_with_actions)

        call_count = 0

        async def counting_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise HomeAssistantError("Always fails")

        with patch(
            "homeassistant.core.ServiceRegistry.async_call",
            side_effect=counting_call,
        ):
            hass.states.async_set(POWER_METER, "3000")
            await hass.async_block_till_done()

        # A resume transition calls start_charging then set_current.
        # Each should be attempted (1 + ACTION_MAX_RETRIES) times.
        expected_calls = 2 * (1 + ACTION_MAX_RETRIES)
        assert call_count == expected_calls


# ---------------------------------------------------------------------------
# Success clears diagnostic error state
# ---------------------------------------------------------------------------


class TestSuccessClearsError:
    """Successful charger action clears the last error and dismisses the failure notification."""

    async def test_success_clears_last_action_error(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """Error diagnostic is cleared after a subsequent successful action execution."""
        await setup_integration(hass, mock_config_entry_with_actions)
        coordinator = _no_sleep_coordinator(hass, mock_config_entry_with_actions)

        # Step 1: Cause a failure
        with patch(
            "homeassistant.core.ServiceRegistry.async_call",
            side_effect=HomeAssistantError("Script not found"),
        ):
            hass.states.async_set(POWER_METER, "3000")
            await hass.async_block_till_done()

        assert coordinator.last_action_error is not None

        # Step 2: Successful action clears error
        calls = async_mock_service(hass, "script", "turn_on")
        hass.states.async_set(POWER_METER, "8000")
        await hass.async_block_till_done()

        assert coordinator.last_action_error is None
        assert coordinator.last_action_timestamp is not None

    async def test_success_dismisses_action_failed_notification(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """Action-failed persistent notification is dismissed when a subsequent action succeeds."""
        await setup_integration(hass, mock_config_entry_with_actions)
        coordinator = _no_sleep_coordinator(hass, mock_config_entry_with_actions)

        # Step 1: Cause a failure to create the notification
        with patch(PN_CREATE), patch(PN_DISMISS), patch(
            "homeassistant.core.ServiceRegistry.async_call",
            side_effect=HomeAssistantError("Script not found"),
        ):
            hass.states.async_set(POWER_METER, "3000")
            await hass.async_block_till_done()

        # Step 2: Successful action should dismiss the notification
        with patch(PN_DISMISS) as mock_dismiss:
            calls = async_mock_service(hass, "script", "turn_on")
            hass.states.async_set(POWER_METER, "8000")
            await hass.async_block_till_done()

        expected_notification_id = NOTIFICATION_ACTION_FAILED_FMT.format(
            entry_id=mock_config_entry_with_actions.entry_id
        )
        dismiss_ids = [str(c) for c in mock_dismiss.call_args_list]
        assert any(expected_notification_id in d for d in dismiss_ids)


# ---------------------------------------------------------------------------
# Diagnostic sensors
# ---------------------------------------------------------------------------


class TestDiagnosticSensors:
    """Diagnostic sensors expose action error and timestamp for debugging."""

    async def test_last_action_error_sensor_defaults_to_none(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """Error sensor shows no error when no actions have failed."""
        await setup_integration(hass, mock_config_entry_with_actions)

        sensor_id = get_entity_id(
            hass, mock_config_entry_with_actions, "sensor", "last_action_error"
        )
        state = hass.states.get(sensor_id)
        assert state is not None
        # None/unknown state before any action
        assert state.state in ("unknown", "None", "none")

    async def test_last_action_timestamp_sensor_defaults_to_none(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """Timestamp sensor shows no timestamp when no actions have executed."""
        await setup_integration(hass, mock_config_entry_with_actions)

        sensor_id = get_entity_id(
            hass, mock_config_entry_with_actions, "sensor", "last_action_timestamp"
        )
        state = hass.states.get(sensor_id)
        assert state is not None
        assert state.state in ("unknown", "None", "none")

    async def test_last_action_error_sensor_shows_error_after_failure(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """Error sensor displays the failure reason after all retry attempts are exhausted."""
        await setup_integration(hass, mock_config_entry_with_actions)
        _no_sleep_coordinator(hass, mock_config_entry_with_actions)

        with patch(
            "homeassistant.core.ServiceRegistry.async_call",
            side_effect=HomeAssistantError("Script not found"),
        ):
            hass.states.async_set(POWER_METER, "3000")
            await hass.async_block_till_done()

        sensor_id = get_entity_id(
            hass, mock_config_entry_with_actions, "sensor", "last_action_error"
        )
        state = hass.states.get(sensor_id)
        assert state is not None
        assert "Script not found" in state.state

    async def test_last_action_timestamp_sensor_updates_on_success(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """Timestamp sensor records the time of the last successful action execution."""
        calls = async_mock_service(hass, "script", "turn_on")
        await setup_integration(hass, mock_config_entry_with_actions)

        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        sensor_id = get_entity_id(
            hass, mock_config_entry_with_actions, "sensor", "last_action_timestamp"
        )
        state = hass.states.get(sensor_id)
        assert state is not None
        # Should be an ISO timestamp
        assert "T" in state.state
        assert state.state != "unknown"
