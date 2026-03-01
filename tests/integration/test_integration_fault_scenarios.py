"""End-to-end integration tests for fault scenarios and compound failures.

Tests cover scenarios not exercised by individual unit tests:
- Action script failures during meter fallback transitions (compound fault)
- Service-call timeouts (asyncio.TimeoutError) handled with retry/backoff
- Health/fault binary sensor entity transitions during action failures
- Action failures during meter recovery transitions
- Meter flapping with concurrent action failures (compound fault)
"""

import asyncio
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.ev_lb.const import (
    ACTION_MAX_RETRIES,
    CONF_ACTION_SET_CURRENT,
    CONF_ACTION_START_CHARGING,
    CONF_ACTION_STOP_CHARGING,
    CONF_MAX_SERVICE_CURRENT,
    CONF_POWER_METER_ENTITY,
    CONF_UNAVAILABLE_BEHAVIOR,
    CONF_UNAVAILABLE_FALLBACK_CURRENT,
    CONF_VOLTAGE,
    DOMAIN,
    EVENT_ACTION_FAILED,
    EVENT_METER_UNAVAILABLE,
    REASON_FALLBACK_UNAVAILABLE,
    REASON_POWER_METER_UPDATE,
    UNAVAILABLE_BEHAVIOR_SET_CURRENT,
)
from conftest import (
    POWER_METER,
    SET_CURRENT_SCRIPT,
    STOP_CHARGING_SCRIPT,
    START_CHARGING_SCRIPT,
    collect_events,
    get_entity_id,
    no_sleep_coordinator,
    setup_integration,
    PN_CREATE,
    PN_DISMISS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry_with_actions_and_fallback(behavior: str, fallback_a: float = 10.0) -> MockConfigEntry:
    """Create a config entry with action scripts and a specific fallback behavior.

    ``behavior`` must be one of the ``UNAVAILABLE_BEHAVIOR_*`` constants
    (``"stop"``, ``"ignore"``, ``"set_current"``).  ``fallback_a`` is the
    safe current (Amps) applied in ``set_current`` mode when the meter is
    unavailable.
    """
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_POWER_METER_ENTITY: POWER_METER,
            CONF_VOLTAGE: 230.0,
            CONF_MAX_SERVICE_CURRENT: 32.0,
            CONF_ACTION_SET_CURRENT: SET_CURRENT_SCRIPT,
            CONF_ACTION_STOP_CHARGING: STOP_CHARGING_SCRIPT,
            CONF_ACTION_START_CHARGING: START_CHARGING_SCRIPT,
            CONF_UNAVAILABLE_BEHAVIOR: behavior,
            CONF_UNAVAILABLE_FALLBACK_CURRENT: fallback_a,
        },
        title="EV Load Balancing",
    )


# ---------------------------------------------------------------------------
# Compound fault: action failure during meter fallback
# ---------------------------------------------------------------------------


class TestActionFailureDuringMeterFallback:
    """Charger state remains safe when action scripts fail during a meter unavailability transition.

    When the power meter goes unavailable and the stop_charging action script
    also fails, the coordinator must still apply the fallback current (0 A for
    stop mode) and update all health entities correctly.
    """

    async def test_stop_mode_fallback_applies_despite_action_failure(
        self, hass: HomeAssistant,
    ) -> None:
        """Charging stops and health entities update even when the stop action script fails."""
        entry = _entry_with_actions_and_fallback("stop")
        await setup_integration(hass, entry)
        coordinator = no_sleep_coordinator(hass, entry)
        coordinator.ramp_up_time_s = 0.0

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")
        active_id = get_entity_id(hass, entry, "binary_sensor", "active")
        meter_status_id = get_entity_id(hass, entry, "binary_sensor", "meter_status")
        fallback_active_id = get_entity_id(hass, entry, "binary_sensor", "fallback_active")
        reason_id = get_entity_id(hass, entry, "sensor", "last_action_reason")
        action_status_id = get_entity_id(hass, entry, "sensor", "last_action_status")
        action_error_id = get_entity_id(hass, entry, "sensor", "last_action_error")

        meter_events = collect_events(hass, EVENT_METER_UNAVAILABLE)
        action_events = collect_events(hass, EVENT_ACTION_FAILED)

        # Phase 1: Normal charging at 18 A
        async_mock_service(hass, "script", "turn_on")
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 18.0
        assert hass.states.get(active_id).state == "on"

        # Phase 2: Meter goes unavailable AND action scripts fail
        with patch(
            "homeassistant.core.ServiceRegistry.async_call",
            side_effect=HomeAssistantError("Charger offline"),
        ):
            hass.states.async_set(POWER_METER, "unavailable")
            await hass.async_block_till_done()

        # Fallback must still apply — coordinator state is correct regardless of action failure
        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(active_id).state == "off"
        assert hass.states.get(meter_status_id).state == "off"
        assert hass.states.get(fallback_active_id).state == "on"
        assert hass.states.get(reason_id).state == REASON_FALLBACK_UNAVAILABLE

        # Both fault types should be signaled
        assert len(meter_events) >= 1
        assert len(action_events) >= 1

        # Diagnostic sensors should reflect the action failure
        assert hass.states.get(action_status_id).state == "failure"
        assert "Charger offline" in hass.states.get(action_error_id).state

    async def test_set_current_fallback_applies_despite_action_failure(
        self, hass: HomeAssistant,
    ) -> None:
        """Fallback current is applied and health entities update even when the set_current action fails."""
        entry = _entry_with_actions_and_fallback("set_current", fallback_a=8.0)
        await setup_integration(hass, entry)
        coordinator = no_sleep_coordinator(hass, entry)

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")
        active_id = get_entity_id(hass, entry, "binary_sensor", "active")
        meter_status_id = get_entity_id(hass, entry, "binary_sensor", "meter_status")
        fallback_active_id = get_entity_id(hass, entry, "binary_sensor", "fallback_active")

        # Phase 1: Normal charging
        async_mock_service(hass, "script", "turn_on")
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 18.0

        # Phase 2: Meter goes unavailable AND action scripts fail
        with patch(
            "homeassistant.core.ServiceRegistry.async_call",
            side_effect=HomeAssistantError("Connection refused"),
        ):
            hass.states.async_set(POWER_METER, "unavailable")
            await hass.async_block_till_done()

        # Fallback current must still be applied to coordinator state
        assert float(hass.states.get(current_set_id).state) == 8.0
        assert hass.states.get(active_id).state == "on"
        assert hass.states.get(meter_status_id).state == "off"
        assert hass.states.get(fallback_active_id).state == "on"


# ---------------------------------------------------------------------------
# Service-call timeout (asyncio.TimeoutError)
# ---------------------------------------------------------------------------


class TestServiceCallTimeout:
    """Charger command timeouts are handled identically to other communication failures.

    asyncio.TimeoutError is a distinct exception type that some HA integrations
    raise when a charger does not respond in time. The retry/backoff logic must
    treat it the same as HomeAssistantError.
    """

    async def test_timeout_triggers_retry_and_records_failure(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """Charger command timeout triggers retries and records failure after exhaustion."""
        await setup_integration(hass, mock_config_entry_with_actions)
        coordinator = no_sleep_coordinator(hass, mock_config_entry_with_actions)
        events = collect_events(hass, EVENT_ACTION_FAILED)

        with patch(
            "homeassistant.core.ServiceRegistry.async_call",
            side_effect=asyncio.TimeoutError(),
        ):
            hass.states.async_set(POWER_METER, "3000")
            await hass.async_block_till_done()

        # Failure events should fire after retries are exhausted
        assert len(events) >= 1

        # Diagnostic state should reflect the timeout failure
        assert coordinator.last_action_error is not None
        assert coordinator.last_action_status == "failure"
        assert coordinator.retry_count == ACTION_MAX_RETRIES

        # Sleep should have been called with exponential backoff delays.
        # Each failing action produces ACTION_MAX_RETRIES sleep calls with
        # delays 2^0=1 s, 2^1=2 s, 2^2=4 s (base delay × 2^attempt).
        sleep_calls = [c.args[0] for c in coordinator._sleep_fn.call_args_list]
        expected_pattern = [1.0, 2.0, 4.0]
        # Each failed action produces the same backoff pattern; verify in chunks.
        for i in range(0, len(sleep_calls), ACTION_MAX_RETRIES):
            chunk = sleep_calls[i : i + ACTION_MAX_RETRIES]
            assert chunk == expected_pattern

    async def test_timeout_recovery_clears_error(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """Successful charger command after a timeout clears the error state."""
        await setup_integration(hass, mock_config_entry_with_actions)
        coordinator = no_sleep_coordinator(hass, mock_config_entry_with_actions)

        # Step 1: Cause a timeout failure
        with patch(
            "homeassistant.core.ServiceRegistry.async_call",
            side_effect=asyncio.TimeoutError(),
        ):
            hass.states.async_set(POWER_METER, "3000")
            await hass.async_block_till_done()

        assert coordinator.last_action_status == "failure"

        # Step 2: Next action succeeds — error clears
        async_mock_service(hass, "script", "turn_on")
        hass.states.async_set(POWER_METER, "5000")
        await hass.async_block_till_done()

        assert coordinator.last_action_error is None
        assert coordinator.last_action_status == "success"


# ---------------------------------------------------------------------------
# Health entity transitions during action failures
# ---------------------------------------------------------------------------


class TestHealthEntityTransitionsDuringActionFailure:
    """Health and diagnostic entity states remain correct when action scripts fail.

    The binary sensor entities (active, meter_status, fallback_active) and
    diagnostic sensors must reflect the coordinator's computed state even when
    the action scripts used to physically control the charger are failing.
    """

    async def test_binary_sensors_reflect_active_state_despite_action_failure(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """Active binary sensor reflects the computed charging state even when actions fail."""
        await setup_integration(hass, mock_config_entry_with_actions)
        no_sleep_coordinator(hass, mock_config_entry_with_actions)

        active_id = get_entity_id(hass, mock_config_entry_with_actions, "binary_sensor", "active")
        current_set_id = get_entity_id(hass, mock_config_entry_with_actions, "sensor", "current_set")
        meter_status_id = get_entity_id(hass, mock_config_entry_with_actions, "binary_sensor", "meter_status")

        # Action scripts fail, but balancer should still compute and report correct state
        with patch(
            "homeassistant.core.ServiceRegistry.async_call",
            side_effect=HomeAssistantError("Script broken"),
        ):
            hass.states.async_set(POWER_METER, "3000")
            await hass.async_block_till_done()

        # Coordinator computes 18 A — entities should reflect this
        assert float(hass.states.get(current_set_id).state) == 18.0
        assert hass.states.get(active_id).state == "on"
        assert hass.states.get(meter_status_id).state == "on"

    async def test_diagnostic_sensors_update_after_action_failure_cycle(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """Diagnostic sensors show failure details after action scripts fail."""
        await setup_integration(hass, mock_config_entry_with_actions)
        no_sleep_coordinator(hass, mock_config_entry_with_actions)

        action_status_id = get_entity_id(hass, mock_config_entry_with_actions, "sensor", "last_action_status")
        action_error_id = get_entity_id(hass, mock_config_entry_with_actions, "sensor", "last_action_error")
        retry_count_id = get_entity_id(hass, mock_config_entry_with_actions, "sensor", "retry_count")
        latency_id = get_entity_id(hass, mock_config_entry_with_actions, "sensor", "action_latency")

        with patch(
            "homeassistant.core.ServiceRegistry.async_call",
            side_effect=HomeAssistantError("Charger unreachable"),
        ):
            hass.states.async_set(POWER_METER, "3000")
            await hass.async_block_till_done()

        assert hass.states.get(action_status_id).state == "failure"
        assert "Charger unreachable" in hass.states.get(action_error_id).state
        assert int(hass.states.get(retry_count_id).state) == ACTION_MAX_RETRIES
        assert float(hass.states.get(latency_id).state) >= 0


# ---------------------------------------------------------------------------
# Action failure during recovery from fallback
# ---------------------------------------------------------------------------


class TestActionFailureDuringRecovery:
    """Charging current computes correctly when action scripts fail during meter recovery.

    When the meter comes back online, the coordinator recomputes the correct
    charging target. If the start_charging or set_current action fails at that
    point, the computed current and health entities should still reflect the
    recovered state.
    """

    async def test_recovery_computes_correct_current_despite_action_failure(
        self, hass: HomeAssistant,
    ) -> None:
        """Correct charging current is computed on meter recovery even when actions fail."""
        entry = _entry_with_actions_and_fallback("stop")
        await setup_integration(hass, entry)
        coordinator = no_sleep_coordinator(hass, entry)
        coordinator.ramp_up_time_s = 0.0

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")
        active_id = get_entity_id(hass, entry, "binary_sensor", "active")
        meter_status_id = get_entity_id(hass, entry, "binary_sensor", "meter_status")
        fallback_active_id = get_entity_id(hass, entry, "binary_sensor", "fallback_active")
        reason_id = get_entity_id(hass, entry, "sensor", "last_action_reason")

        # Phase 1: Normal charging
        async_mock_service(hass, "script", "turn_on")
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0

        # Phase 2: Meter goes unavailable → stop fallback
        hass.states.async_set(POWER_METER, "unavailable")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(fallback_active_id).state == "on"

        # Phase 3: Meter recovers but action scripts fail
        with patch(
            "homeassistant.core.ServiceRegistry.async_call",
            side_effect=HomeAssistantError("Charger timeout"),
        ):
            hass.states.async_set(POWER_METER, "3000")
            await hass.async_block_till_done()

        # Computed state should reflect recovery even though actions failed
        assert float(hass.states.get(current_set_id).state) == 18.0
        assert hass.states.get(active_id).state == "on"
        assert hass.states.get(meter_status_id).state == "on"
        assert hass.states.get(fallback_active_id).state == "off"
        assert hass.states.get(reason_id).state == REASON_POWER_METER_UPDATE

        # Action failure is still recorded in diagnostics
        assert coordinator.last_action_status == "failure"
        assert coordinator.last_action_error is not None


# ---------------------------------------------------------------------------
# Compound fault: meter flapping with concurrent action failures
# ---------------------------------------------------------------------------


class TestMeterFlappingWithActionFailures:
    """System reaches a consistent final state when the meter flaps while actions are failing.

    Simulates a real-world scenario where the meter goes unavailable, recovers
    briefly, then goes unavailable again — all while the charger action scripts
    are unreachable.
    """

    async def test_meter_flap_with_action_failures_reaches_consistent_state(
        self, hass: HomeAssistant,
    ) -> None:
        """Rapid meter unavailable/recovery cycles with failing actions result in correct final state."""
        entry = _entry_with_actions_and_fallback("stop")
        await setup_integration(hass, entry)
        coordinator = no_sleep_coordinator(hass, entry)
        coordinator.ramp_up_time_s = 0.0

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")
        active_id = get_entity_id(hass, entry, "binary_sensor", "active")
        meter_status_id = get_entity_id(hass, entry, "binary_sensor", "meter_status")
        fallback_active_id = get_entity_id(hass, entry, "binary_sensor", "fallback_active")

        # Phase 1: Normal charging
        async_mock_service(hass, "script", "turn_on")
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0

        # Phase 2: All actions fail from now on
        with patch(
            "homeassistant.core.ServiceRegistry.async_call",
            side_effect=HomeAssistantError("Network error"),
        ):
            # Meter flap: unavailable → recover → unavailable
            hass.states.async_set(POWER_METER, "unavailable")
            await hass.async_block_till_done()

            hass.states.async_set(POWER_METER, "3000")
            await hass.async_block_till_done()

            hass.states.async_set(POWER_METER, "unavailable")
            await hass.async_block_till_done()

        # Final state: meter unavailable in stop mode → 0 A, fallback active
        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(active_id).state == "off"
        assert hass.states.get(meter_status_id).state == "off"
        assert hass.states.get(fallback_active_id).state == "on"

    async def test_meter_recovers_after_flap_with_action_failures(
        self, hass: HomeAssistant,
    ) -> None:
        """System recovers fully when the meter stabilises and actions start working again."""
        entry = _entry_with_actions_and_fallback("stop")
        await setup_integration(hass, entry)
        coordinator = no_sleep_coordinator(hass, entry)
        coordinator.ramp_up_time_s = 0.0

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")
        active_id = get_entity_id(hass, entry, "binary_sensor", "active")
        meter_status_id = get_entity_id(hass, entry, "binary_sensor", "meter_status")
        fallback_active_id = get_entity_id(hass, entry, "binary_sensor", "fallback_active")

        # Phase 1: Normal charging
        async_mock_service(hass, "script", "turn_on")
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0

        # Phase 2: Meter flaps with failing actions
        with patch(
            "homeassistant.core.ServiceRegistry.async_call",
            side_effect=HomeAssistantError("Network error"),
        ):
            hass.states.async_set(POWER_METER, "unavailable")
            await hass.async_block_till_done()
            hass.states.async_set(POWER_METER, "3000")
            await hass.async_block_till_done()
            hass.states.async_set(POWER_METER, "unavailable")
            await hass.async_block_till_done()

        # Phase 3: Both meter and actions recover
        async_mock_service(hass, "script", "turn_on")
        hass.states.async_set(POWER_METER, "4000")
        await hass.async_block_till_done()

        # Should be back to normal operation
        recovered = float(hass.states.get(current_set_id).state)
        assert recovered > 0
        assert hass.states.get(active_id).state == "on"
        assert hass.states.get(meter_status_id).state == "on"
        assert hass.states.get(fallback_active_id).state == "off"

        # Diagnostic sensors should show the successful recovery
        assert coordinator.last_action_status == "success"
        assert coordinator.last_action_error is None


# ---------------------------------------------------------------------------
# Action failure with set_current fallback during recovery
# ---------------------------------------------------------------------------


class TestFallbackRecoveryWithActionFailure:
    """Fallback-to-recovery transition is correct even when actions fail at the boundary.

    In set_current fallback mode, the transition from fallback current to
    computed current on meter recovery must update all entities correctly
    even if the set_current action fails during that transition.
    """

    async def test_set_current_fallback_to_recovery_with_action_failure(
        self, hass: HomeAssistant,
    ) -> None:
        """Transition from fallback current to computed current is correct despite action failure at recovery."""
        entry = _entry_with_actions_and_fallback("set_current", fallback_a=10.0)
        await setup_integration(hass, entry)
        coordinator = no_sleep_coordinator(hass, entry)

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")
        fallback_active_id = get_entity_id(hass, entry, "binary_sensor", "fallback_active")
        meter_status_id = get_entity_id(hass, entry, "binary_sensor", "meter_status")

        # Phase 1: Normal charging
        async_mock_service(hass, "script", "turn_on")
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0

        # Phase 2: Meter unavailable → fallback to 10 A
        hass.states.async_set(POWER_METER, "unavailable")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 10.0
        assert hass.states.get(fallback_active_id).state == "on"

        # Phase 3: Meter recovers but actions fail
        with patch(
            "homeassistant.core.ServiceRegistry.async_call",
            side_effect=HomeAssistantError("Charger unreachable"),
        ):
            hass.states.async_set(POWER_METER, "3000")
            await hass.async_block_till_done()

        # Coordinator computes from live meter despite action failure.
        # Formula: service=3000W/230V≈13A, ev_estimate=10A (fallback),
        # non_ev=13−10=3A, available=32−3=29A → clamped to charger max.
        recovered = float(hass.states.get(current_set_id).state)
        assert recovered > 0
        assert hass.states.get(fallback_active_id).state == "off"
        assert hass.states.get(meter_status_id).state == "on"
        assert coordinator.last_action_status == "failure"
