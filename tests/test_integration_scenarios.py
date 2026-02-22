"""End-to-end integration scenario tests.

These tests exercise complete user journeys through the full integration
stack — config entry setup, coordinator, entity state updates, action
execution, event notifications, and persistent notifications — without
requiring a real Home Assistant instance or Docker.

Each test class represents a realistic scenario that a user might
encounter during daily operation of the EV charger load balancer.
"""

from unittest.mock import patch

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
    CONF_MAX_SERVICE_CURRENT,
    CONF_POWER_METER_ENTITY,
    CONF_UNAVAILABLE_BEHAVIOR,
    CONF_UNAVAILABLE_FALLBACK_CURRENT,
    CONF_VOLTAGE,
    DOMAIN,
    EVENT_CHARGING_RESUMED,
    EVENT_FALLBACK_ACTIVATED,
    EVENT_METER_UNAVAILABLE,
    EVENT_OVERLOAD_STOP,
    NOTIFICATION_FALLBACK_ACTIVATED_FMT,
    NOTIFICATION_METER_UNAVAILABLE_FMT,
    NOTIFICATION_OVERLOAD_STOP_FMT,
    REASON_FALLBACK_UNAVAILABLE,
    REASON_MANUAL_OVERRIDE,
    REASON_PARAMETER_CHANGE,
    REASON_POWER_METER_UPDATE,
    SERVICE_SET_LIMIT,
    STATE_ACTIVE,
    STATE_ADJUSTING,
    STATE_DISABLED,
    STATE_RAMP_UP_HOLD,
    STATE_STOPPED,
    UNAVAILABLE_BEHAVIOR_SET_CURRENT,
)
from conftest import (
    POWER_METER,
    SET_CURRENT_SCRIPT,
    STOP_CHARGING_SCRIPT,
    START_CHARGING_SCRIPT,
    setup_integration,
    get_entity_id,
)

PN_CREATE = "custom_components.ev_lb.coordinator.pn_async_create"
PN_DISMISS = "custom_components.ev_lb.coordinator.pn_async_dismiss"


def _collect_events(hass: HomeAssistant, event_type: str) -> list[dict]:
    """Subscribe to an event type and return a list of captured event data dicts."""
    captured: list[dict] = []

    def _listener(event):
        captured.append(dict(event.data))

    hass.bus.async_listen(event_type, _listener)
    return captured


# ---------------------------------------------------------------------------
# Scenario 1: A full day of EV charging with varying household loads
# ---------------------------------------------------------------------------


class TestNormalDailyOperation:
    """Simulate a full day of EV charging through varying household load conditions.

    Exercises the complete chain: power meter → coordinator → computation →
    entity updates → action execution for every major transition.
    """

    async def test_full_day_charging_with_actions(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """Charger adapts correctly through low load, moderate load, overload, and recovery."""
        calls = async_mock_service(hass, "script", "turn_on")
        await setup_integration(hass, mock_config_entry_with_actions)
        coordinator = hass.data[DOMAIN][mock_config_entry_with_actions.entry_id]["coordinator"]

        entry_id = mock_config_entry_with_actions.entry_id
        current_set_id = get_entity_id(hass, mock_config_entry_with_actions, "sensor", "current_set")
        available_id = get_entity_id(hass, mock_config_entry_with_actions, "sensor", "available_current")
        active_id = get_entity_id(hass, mock_config_entry_with_actions, "binary_sensor", "active")
        reason_id = get_entity_id(hass, mock_config_entry_with_actions, "sensor", "last_action_reason")
        state_id = get_entity_id(hass, mock_config_entry_with_actions, "sensor", "balancer_state")

        # Use a controllable clock to manage ramp-up cooldown
        mock_time = 1000.0

        def fake_monotonic():
            return mock_time

        coordinator._time_fn = fake_monotonic
        coordinator._ramp_up_time_s = 30.0

        # --- Phase 1: Low household load → charger starts at near-max capacity ---
        # 1000 W at 230 V → draw ~4.3 A → headroom = 32 - 4.3 = 27.7 A → target = 27 A
        hass.states.async_set(POWER_METER, "1000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 27.0
        assert hass.states.get(active_id).state == "on"
        assert hass.states.get(reason_id).state == REASON_POWER_METER_UPDATE

        # start_charging + set_current should fire (resume from stopped)
        assert len(calls) == 2
        assert calls[0].data["entity_id"] == START_CHARGING_SCRIPT
        assert calls[0].data["variables"]["charger_id"] == entry_id
        assert calls[1].data["entity_id"] == SET_CURRENT_SCRIPT
        assert calls[1].data["variables"]["current_a"] == 27.0

        # --- Phase 2: Moderate load increase → charger adjusts down instantly ---
        calls.clear()
        mock_time = 1001.0
        # 5000 W at 230 V → draw ~21.7 A → headroom = 32 - 21.7 = 10.3 A
        # raw_target = 27 + 10.3 = 37.3 → capped at 32 → reduction from 27
        # Wait — headroom = 10.3 means available current = 10.3 A.
        # raw_target = current_set (27) + available (10.3) = 37.3
        # Actually the available_a is from compute_available_current which is:
        #   service_current - house_power_w / voltage = 32 - 5000/230 = 32 - 21.74 = 10.26
        # raw_target = 27 + 10.26 = 37.26 → clamped to 32 → floor → 32
        # But wait, 32 > 27 (increase), and we just had a reduction at t=1001? No, no reduction yet.
        # Actually the first event sets current_set_a to 27, no reduction happened.
        # So raw_target = 27 + 10.26 = 37.26 → capped at 32 A → increase from 27 → 32 A
        # No reduction has occurred yet, so ramp-up is not triggered.
        hass.states.async_set(POWER_METER, "5000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 32.0
        # Only set_current (adjust, not resume — already active)
        assert len(calls) == 1
        assert calls[0].data["entity_id"] == SET_CURRENT_SCRIPT
        assert calls[0].data["variables"]["current_a"] == 32.0

        # --- Phase 3: Heavy load spike → instant reduction ---
        calls.clear()
        mock_time = 1010.0
        # 8000 W at 230 V → available = 32 - 34.78 = -2.78 A
        # raw_target = 32 + (-2.78) = 29.22 → clamped = 29 A → reduction
        hass.states.async_set(POWER_METER, "8000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 29.0
        assert hass.states.get(active_id).state == "on"
        # set_current fires for the adjustment
        assert len(calls) == 1
        assert calls[0].data["variables"]["current_a"] == 29.0

        # --- Phase 4: Extreme overload → charger stops ---
        calls.clear()
        mock_time = 1020.0
        # 12000 W at 230 V → available = 32 - 52.17 = -20.17 A
        # raw_target = 29 + (-20.17) = 8.83 → clamped to 8 A
        # Wait: but 8 > 6 (min), so it shouldn't stop. Let me use a higher load.
        # Actually the reduction from 29→8 is instant. But then current = 8 A, still > 6 min.
        # Use 14000 W: available = 32 - 60.87 = -28.87, raw = 29 + (-28.87) = 0.13 → < 6 → stop → 0
        hass.states.async_set(POWER_METER, "14000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(active_id).state == "off"
        # stop_charging fires
        stop_calls = [c for c in calls if c.data["entity_id"] == STOP_CHARGING_SCRIPT]
        assert len(stop_calls) == 1

        # --- Phase 5: Load drops, but within ramp-up cooldown → held ---
        calls.clear()
        mock_time = 1025.0  # Only 5s after last reduction at t=1020 (< 30s cooldown)
        # 3000 W at 230 V → available = 32 - 13.04 = 18.96
        # raw_target = 0 + 18.96 = 18.96 → clamped to 18 A
        # apply_ramp_up_limit: increase from 0→18, but last_reduction at t=1020
        #   elapsed = 1025 - 1020 = 5 < 30 → hold at 0
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(active_id).state == "off"
        assert len(calls) == 0  # No actions while held

        # --- Phase 6: Cooldown expires → charger resumes ---
        calls.clear()
        mock_time = 1051.0  # 31s after reduction at t=1020 (> 30s cooldown)
        hass.states.async_set(POWER_METER, "3001")
        await hass.async_block_till_done()

        resumed_current = float(hass.states.get(current_set_id).state)
        assert resumed_current > 0
        assert hass.states.get(active_id).state == "on"
        # start_charging + set_current should fire (resume from stopped)
        assert len(calls) == 2
        assert calls[0].data["entity_id"] == START_CHARGING_SCRIPT
        assert calls[1].data["entity_id"] == SET_CURRENT_SCRIPT
        assert calls[1].data["variables"]["current_a"] == resumed_current


# ---------------------------------------------------------------------------
# Scenario 2: Meter failure and recovery with fault notifications
# ---------------------------------------------------------------------------


class TestMeterFailureAndRecovery:
    """Simulate meter becoming unavailable and recovering, with full event/notification tracking.

    Verifies the complete chain: meter loss → fallback action → fault event →
    persistent notification → meter recovery → normal computation → notification dismissed.
    """

    async def test_stop_mode_full_cycle(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Charging stops on meter loss, notifications appear, and everything resumes when meter recovers."""
        with patch(PN_CREATE) as mock_create, patch(PN_DISMISS) as mock_dismiss:
            await setup_integration(hass, mock_config_entry)
            coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
            coordinator._ramp_up_time_s = 0.0  # Disable cooldown for clean transitions

            entry_id = mock_config_entry.entry_id
            current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")
            active_id = get_entity_id(hass, mock_config_entry, "binary_sensor", "active")
            meter_status_id = get_entity_id(hass, mock_config_entry, "binary_sensor", "meter_status")
            fallback_active_id = get_entity_id(hass, mock_config_entry, "binary_sensor", "fallback_active")
            reason_id = get_entity_id(hass, mock_config_entry, "sensor", "last_action_reason")

            meter_events = _collect_events(hass, EVENT_METER_UNAVAILABLE)
            resumed_events = _collect_events(hass, EVENT_CHARGING_RESUMED)

            # Phase 1: Normal charging
            hass.states.async_set(POWER_METER, "3000")
            await hass.async_block_till_done()

            assert float(hass.states.get(current_set_id).state) == 18.0
            assert hass.states.get(active_id).state == "on"
            assert hass.states.get(meter_status_id).state == "on"
            assert hass.states.get(fallback_active_id).state == "off"

            mock_create.reset_mock()

            # Phase 2: Meter goes unavailable → stop mode kicks in
            hass.states.async_set(POWER_METER, "unavailable")
            await hass.async_block_till_done()

            assert float(hass.states.get(current_set_id).state) == 0.0
            assert hass.states.get(active_id).state == "off"
            assert hass.states.get(meter_status_id).state == "off"
            assert hass.states.get(fallback_active_id).state == "on"
            assert hass.states.get(reason_id).state == REASON_FALLBACK_UNAVAILABLE

            # Event and notification should fire
            assert len(meter_events) == 1
            assert meter_events[0]["entry_id"] == entry_id
            mock_create.assert_called_once()
            notification_id = NOTIFICATION_METER_UNAVAILABLE_FMT.format(entry_id=entry_id)
            assert notification_id in str(mock_create.call_args)

            mock_dismiss.reset_mock()

            # Phase 3: Meter recovers → normal computation resumes
            hass.states.async_set(POWER_METER, "3000")
            await hass.async_block_till_done()

            assert float(hass.states.get(current_set_id).state) > 0
            assert hass.states.get(active_id).state == "on"
            assert hass.states.get(meter_status_id).state == "on"
            assert hass.states.get(fallback_active_id).state == "off"
            assert hass.states.get(reason_id).state == REASON_POWER_METER_UPDATE

            # Resume event should fire
            resumed_after_recovery = [e for e in resumed_events if e["current_a"] > 0]
            assert len(resumed_after_recovery) >= 1

            # Meter notification should be dismissed
            dismiss_ids = [call.args[1] for call in mock_dismiss.call_args_list]
            assert notification_id in dismiss_ids

    async def test_fallback_mode_full_cycle(
        self, hass: HomeAssistant, mock_config_entry_fallback: MockConfigEntry
    ) -> None:
        """Fallback current is applied on meter loss and normal computation resumes on recovery."""
        with patch(PN_CREATE) as mock_create, patch(PN_DISMISS) as mock_dismiss:
            await setup_integration(hass, mock_config_entry_fallback)

            entry_id = mock_config_entry_fallback.entry_id
            current_set_id = get_entity_id(hass, mock_config_entry_fallback, "sensor", "current_set")
            active_id = get_entity_id(hass, mock_config_entry_fallback, "binary_sensor", "active")
            meter_status_id = get_entity_id(hass, mock_config_entry_fallback, "binary_sensor", "meter_status")
            fallback_active_id = get_entity_id(hass, mock_config_entry_fallback, "binary_sensor", "fallback_active")

            fallback_events = _collect_events(hass, EVENT_FALLBACK_ACTIVATED)

            # Phase 1: Normal charging at 18 A
            hass.states.async_set(POWER_METER, "3000")
            await hass.async_block_till_done()

            assert float(hass.states.get(current_set_id).state) == 18.0
            assert hass.states.get(active_id).state == "on"

            mock_create.reset_mock()

            # Phase 2: Meter goes unavailable → fallback to 10 A (config)
            hass.states.async_set(POWER_METER, "unavailable")
            await hass.async_block_till_done()

            assert float(hass.states.get(current_set_id).state) == 10.0
            assert hass.states.get(active_id).state == "on"  # Still charging at fallback
            assert hass.states.get(meter_status_id).state == "off"
            assert hass.states.get(fallback_active_id).state == "on"

            # Fallback event + notification
            assert len(fallback_events) == 1
            assert fallback_events[0]["fallback_current_a"] == 10.0
            mock_create.assert_called_once()
            notification_id = NOTIFICATION_FALLBACK_ACTIVATED_FMT.format(entry_id=entry_id)
            assert notification_id in str(mock_create.call_args)

            mock_dismiss.reset_mock()

            # Phase 3: Meter recovers → resumes normal computation
            hass.states.async_set(POWER_METER, "3000")
            await hass.async_block_till_done()

            recovered = float(hass.states.get(current_set_id).state)
            assert recovered > 0
            assert hass.states.get(meter_status_id).state == "on"
            assert hass.states.get(fallback_active_id).state == "off"

            # Fallback notification dismissed
            dismiss_ids = [call.args[1] for call in mock_dismiss.call_args_list]
            assert notification_id in dismiss_ids

    async def test_ignore_mode_full_cycle(
        self, hass: HomeAssistant, mock_config_entry_ignore: MockConfigEntry
    ) -> None:
        """Last value is kept on meter loss, no events fire, and normal computation resumes silently."""
        await setup_integration(hass, mock_config_entry_ignore)

        current_set_id = get_entity_id(hass, mock_config_entry_ignore, "sensor", "current_set")
        active_id = get_entity_id(hass, mock_config_entry_ignore, "binary_sensor", "active")
        meter_status_id = get_entity_id(hass, mock_config_entry_ignore, "binary_sensor", "meter_status")

        meter_events = _collect_events(hass, EVENT_METER_UNAVAILABLE)
        fallback_events = _collect_events(hass, EVENT_FALLBACK_ACTIVATED)

        # Phase 1: Normal charging at 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 18.0
        assert hass.states.get(active_id).state == "on"

        # Phase 2: Meter goes unavailable → ignore mode keeps last value
        hass.states.async_set(POWER_METER, "unavailable")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 18.0  # Unchanged
        assert hass.states.get(active_id).state == "on"  # Still active
        assert hass.states.get(meter_status_id).state == "off"

        # No events should fire in ignore mode
        assert len(meter_events) == 0
        assert len(fallback_events) == 0

        # Phase 3: Meter recovers → normal computation resumes
        hass.states.async_set(POWER_METER, "5000")
        await hass.async_block_till_done()

        # Should now compute from actual meter value
        recovered = float(hass.states.get(current_set_id).state)
        assert recovered > 0
        assert hass.states.get(meter_status_id).state == "on"


# ---------------------------------------------------------------------------
# Scenario 3: Runtime parameter changes during active charging
# ---------------------------------------------------------------------------


class TestParameterChangesDuringCharging:
    """User adjusts charger parameters while charging is active.

    Verifies that max charger current, min EV current, and switch changes
    all take immediate effect without waiting for a new meter event.
    """

    async def test_parameter_cascade_with_actions(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """Lowering max current caps the charger, raising min EV threshold stops it, and auto-resume restores charging."""
        calls = async_mock_service(hass, "script", "turn_on")
        await setup_integration(hass, mock_config_entry_with_actions)
        coordinator = hass.data[DOMAIN][mock_config_entry_with_actions.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0  # Disable cooldown for clean transitions

        current_set_id = get_entity_id(hass, mock_config_entry_with_actions, "sensor", "current_set")
        active_id = get_entity_id(hass, mock_config_entry_with_actions, "binary_sensor", "active")
        max_current_id = get_entity_id(hass, mock_config_entry_with_actions, "number", "max_charger_current")
        min_current_id = get_entity_id(hass, mock_config_entry_with_actions, "number", "min_ev_current")
        reason_id = get_entity_id(hass, mock_config_entry_with_actions, "sensor", "last_action_reason")

        # Phase 1: Start charging at 18 A (3000 W at 230 V)
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 18.0
        assert hass.states.get(active_id).state == "on"

        calls.clear()

        # Phase 2: Lower max charger current to 10 A → immediate cap
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": max_current_id, "value": 10.0},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 10.0
        assert hass.states.get(active_id).state == "on"
        assert hass.states.get(reason_id).state == REASON_PARAMETER_CHANGE

        # set_current action should fire for the adjustment
        set_calls = [c for c in calls if c.data["entity_id"] == SET_CURRENT_SCRIPT]
        assert len(set_calls) >= 1
        assert set_calls[-1].data["variables"]["current_a"] == 10.0

        calls.clear()

        # Phase 3: Raise min EV current to 12 A → current (10) < min (12) → stop
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": min_current_id, "value": 12.0},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(active_id).state == "off"

        # stop_charging action should fire
        stop_calls = [c for c in calls if c.data["entity_id"] == STOP_CHARGING_SCRIPT]
        assert len(stop_calls) >= 1

        calls.clear()

        # Phase 4: Lower min EV current back to 6 A → recompute → resume
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": min_current_id, "value": 6.0},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 10.0  # Capped at max=10
        assert hass.states.get(active_id).state == "on"

        # start_charging + set_current should fire (resume)
        assert len(calls) == 2
        assert calls[0].data["entity_id"] == START_CHARGING_SCRIPT
        assert calls[1].data["entity_id"] == SET_CURRENT_SCRIPT


# ---------------------------------------------------------------------------
# Scenario 4: Manual override then automatic resume
# ---------------------------------------------------------------------------


class TestManualOverrideAndResume:
    """User manually overrides charger current, then automatic balancing resumes.

    Verifies the full cycle: manual set_limit → action fires → reason changes →
    next meter event → automatic balancing resumes → reason reverts.
    """

    async def test_override_cycle_with_full_observability(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """Manual override takes effect with correct actions and reason, then auto-balancing resumes on next meter event."""
        calls = async_mock_service(hass, "script", "turn_on")
        await setup_integration(hass, mock_config_entry_with_actions)

        current_set_id = get_entity_id(hass, mock_config_entry_with_actions, "sensor", "current_set")
        active_id = get_entity_id(hass, mock_config_entry_with_actions, "binary_sensor", "active")
        reason_id = get_entity_id(hass, mock_config_entry_with_actions, "sensor", "last_action_reason")
        state_id = get_entity_id(hass, mock_config_entry_with_actions, "sensor", "balancer_state")

        # Phase 1: Normal charging at 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 18.0
        assert hass.states.get(reason_id).state == REASON_POWER_METER_UPDATE

        calls.clear()

        # Phase 2: Manual override to 10 A
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_LIMIT,
            {"current_a": 10.0},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 10.0
        assert hass.states.get(active_id).state == "on"
        assert hass.states.get(reason_id).state == REASON_MANUAL_OVERRIDE

        # set_current action should fire for the adjustment
        set_calls = [c for c in calls if c.data["entity_id"] == SET_CURRENT_SCRIPT]
        assert len(set_calls) >= 1
        assert set_calls[-1].data["variables"]["current_a"] == 10.0

        calls.clear()

        # Phase 3: Next meter event → automatic balancing resumes
        # 3000 W → available = 18.96, raw_target = 10 + 18.96 = 28.96 → 28 A
        hass.states.async_set(POWER_METER, "3002")
        await hass.async_block_till_done()

        auto_value = float(hass.states.get(current_set_id).state)
        assert auto_value > 10.0  # No longer at manual override value
        assert hass.states.get(reason_id).state == REASON_POWER_METER_UPDATE

        # set_current action should fire for the adjustment
        set_calls = [c for c in calls if c.data["entity_id"] == SET_CURRENT_SCRIPT]
        assert len(set_calls) >= 1


# ---------------------------------------------------------------------------
# Scenario 5: Disable/enable switch during active operation
# ---------------------------------------------------------------------------


class TestSwitchToggleDuringOperation:
    """User disables load balancing during active charging, then re-enables it.

    Verifies that meter events are ignored while disabled, and that
    re-enabling triggers an immediate recompute from the current meter value.
    """

    async def test_disable_ignores_meter_then_reenable_recomputes(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Meter events are ignored while disabled, and re-enabling triggers immediate recompute with correct state."""
        await setup_integration(hass, mock_config_entry)

        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")
        active_id = get_entity_id(hass, mock_config_entry, "binary_sensor", "active")
        switch_id = get_entity_id(hass, mock_config_entry, "switch", "enabled")
        state_id = get_entity_id(hass, mock_config_entry, "sensor", "balancer_state")

        # Phase 1: Normal charging at 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        value_before_disable = float(hass.states.get(current_set_id).state)
        assert value_before_disable == 18.0
        assert hass.states.get(active_id).state == "on"

        # Phase 2: Disable load balancing
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": switch_id}, blocking=True
        )
        await hass.async_block_till_done()

        # Phase 3: Meter changes while disabled → should be ignored
        hass.states.async_set(POWER_METER, "8000")
        await hass.async_block_till_done()

        # Current should remain unchanged; balancer_state set to disabled on meter event
        assert float(hass.states.get(current_set_id).state) == value_before_disable
        assert hass.states.get(state_id).state == STATE_DISABLED

        # Phase 4: Change meter to a different value (still disabled)
        hass.states.async_set(POWER_METER, "5000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == value_before_disable

        # Phase 5: Re-enable → immediate recompute from current meter value (5000 W)
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": switch_id}, blocking=True
        )
        await hass.async_block_till_done()

        recomputed = float(hass.states.get(current_set_id).state)
        # Should reflect 5000 W meter reading, not the old 3000 W or 8000 W
        # raw_target = 18 + (32 - 5000/230) = 18 + 10.26 = 28.26 → 28 A
        assert recomputed > 0
        assert hass.states.get(state_id).state != STATE_DISABLED


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
# Scenario 7: Ramp-up cooldown through all phases
# ---------------------------------------------------------------------------


class TestRampUpCooldownFullCycle:
    """Walk through every phase of the ramp-up cooldown mechanism.

    Verifies the balancer state transitions: active → adjusting (reduction) →
    ramp_up_hold (cooldown active) → adjusting (cooldown expired, increase allowed).
    """

    async def test_cooldown_phases_with_state_tracking(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Balancer state correctly transitions through reduction, hold, and release phases."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 30.0

        mock_time = 1000.0

        def fake_monotonic():
            return mock_time

        coordinator._time_fn = fake_monotonic

        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")
        state_id = get_entity_id(hass, mock_config_entry, "sensor", "balancer_state")

        # Phase 1: Start charging at 18 A (3000 W)
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 18.0
        # First charge: adjusting (transition from stopped → active)
        assert hass.states.get(state_id).state == STATE_ADJUSTING

        # Phase 2: Load increases → reduction → adjusting
        mock_time = 1001.0
        hass.states.async_set(POWER_METER, "8000")
        await hass.async_block_till_done()

        reduced_value = float(hass.states.get(current_set_id).state)
        assert reduced_value < 18.0
        assert hass.states.get(state_id).state == STATE_ADJUSTING

        # Phase 3: Load drops within cooldown → increase held → ramp_up_hold
        mock_time = 1010.0  # 9s after reduction (< 30s)
        hass.states.async_set(POWER_METER, "3002")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == reduced_value  # Held
        assert hass.states.get(state_id).state == STATE_RAMP_UP_HOLD

        # Phase 4: Cooldown expires → increase allowed → adjusting
        mock_time = 1032.0  # 31s after reduction (> 30s)
        hass.states.async_set(POWER_METER, "3003")
        await hass.async_block_till_done()

        after_cooldown = float(hass.states.get(current_set_id).state)
        assert after_cooldown > reduced_value  # Increase now allowed
        assert hass.states.get(state_id).state == STATE_ADJUSTING


# ---------------------------------------------------------------------------
# Scenario 8: Overload with complete event and action chain
# ---------------------------------------------------------------------------


class TestOverloadWithEventAndActionChain:
    """Simulate an overload that triggers stop action, events, and notifications,
    then verify full recovery.

    Combines action execution, event notifications, and persistent notification
    management in a single end-to-end flow.
    """

    async def test_overload_stop_and_recovery_full_chain(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """Overload triggers stop action + event + notification, and recovery restores everything."""
        calls = async_mock_service(hass, "script", "turn_on")

        with patch(PN_CREATE) as mock_create, patch(PN_DISMISS) as mock_dismiss:
            await setup_integration(hass, mock_config_entry_with_actions)
            coordinator = hass.data[DOMAIN][mock_config_entry_with_actions.entry_id]["coordinator"]
            coordinator._ramp_up_time_s = 0.0  # Disable cooldown for clean resume

            entry_id = mock_config_entry_with_actions.entry_id
            current_set_id = get_entity_id(hass, mock_config_entry_with_actions, "sensor", "current_set")
            active_id = get_entity_id(hass, mock_config_entry_with_actions, "binary_sensor", "active")
            state_id = get_entity_id(hass, mock_config_entry_with_actions, "sensor", "balancer_state")

            overload_events = _collect_events(hass, EVENT_OVERLOAD_STOP)
            resumed_events = _collect_events(hass, EVENT_CHARGING_RESUMED)

            # Phase 1: Start charging at 18 A
            hass.states.async_set(POWER_METER, "3000")
            await hass.async_block_till_done()

            assert float(hass.states.get(current_set_id).state) == 18.0
            assert hass.states.get(active_id).state == "on"

            calls.clear()
            mock_create.reset_mock()

            # Phase 2: Extreme overload → stop
            hass.states.async_set(POWER_METER, "14000")
            await hass.async_block_till_done()

            # Entity states
            assert float(hass.states.get(current_set_id).state) == 0.0
            assert hass.states.get(active_id).state == "off"
            assert hass.states.get(state_id).state == STATE_STOPPED

            # Actions: stop_charging should fire
            stop_calls = [c for c in calls if c.data["entity_id"] == STOP_CHARGING_SCRIPT]
            assert len(stop_calls) == 1
            assert stop_calls[0].data["variables"]["charger_id"] == entry_id

            # Events: overload event with correct payload
            assert len(overload_events) == 1
            assert overload_events[0]["entry_id"] == entry_id
            assert overload_events[0]["previous_current_a"] == 18.0

            # Notification: overload notification created
            mock_create.assert_called_once()
            overload_notif_id = NOTIFICATION_OVERLOAD_STOP_FMT.format(entry_id=entry_id)
            assert overload_notif_id in str(mock_create.call_args)

            calls.clear()
            mock_dismiss.reset_mock()

            # Phase 3: Load drops → charger resumes
            hass.states.async_set(POWER_METER, "3000")
            await hass.async_block_till_done()

            # Entity states
            resumed_current = float(hass.states.get(current_set_id).state)
            assert resumed_current > 0
            assert hass.states.get(active_id).state == "on"

            # Actions: start_charging + set_current should fire
            assert len(calls) == 2
            assert calls[0].data["entity_id"] == START_CHARGING_SCRIPT
            assert calls[1].data["entity_id"] == SET_CURRENT_SCRIPT
            assert calls[1].data["variables"]["current_a"] == resumed_current

            # Events: charging resumed
            resume_after_overload = [
                e for e in resumed_events if e["current_a"] > 0
            ]
            assert len(resume_after_overload) >= 1

            # Notification: overload notification dismissed
            dismiss_ids = [call.args[1] for call in mock_dismiss.call_args_list]
            assert overload_notif_id in dismiss_ids


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


# ---------------------------------------------------------------------------
# Scenario 10: Disable/enable with actions — verify silence while disabled
# ---------------------------------------------------------------------------


class TestDisableEnableWithActions:
    """User disables load balancing while actions are configured.

    Verifies that no charger action scripts fire while disabled, and that
    actions resume correctly when re-enabled with a new meter reading.
    """

    async def test_no_actions_while_disabled_then_resume_on_reenable(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """No charger commands are sent while disabled; re-enabling fires the correct resume actions."""
        calls = async_mock_service(hass, "script", "turn_on")
        await setup_integration(hass, mock_config_entry_with_actions)

        current_set_id = get_entity_id(hass, mock_config_entry_with_actions, "sensor", "current_set")
        active_id = get_entity_id(hass, mock_config_entry_with_actions, "binary_sensor", "active")
        switch_id = get_entity_id(hass, mock_config_entry_with_actions, "switch", "enabled")
        state_id = get_entity_id(hass, mock_config_entry_with_actions, "sensor", "balancer_state")

        # Phase 1: Start charging at 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 18.0
        assert hass.states.get(active_id).state == "on"

        calls.clear()

        # Phase 2: Disable load balancing
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": switch_id}, blocking=True
        )
        await hass.async_block_till_done()

        # Phase 3: Multiple meter changes while disabled → no actions should fire
        hass.states.async_set(POWER_METER, "8000")
        await hass.async_block_till_done()

        hass.states.async_set(POWER_METER, "1000")
        await hass.async_block_till_done()

        hass.states.async_set(POWER_METER, "5000")
        await hass.async_block_till_done()

        assert len(calls) == 0  # No actions while disabled
        assert hass.states.get(state_id).state == STATE_DISABLED

        # Phase 4: Re-enable → immediate recompute + actions fire
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": switch_id}, blocking=True
        )
        await hass.async_block_till_done()

        recomputed = float(hass.states.get(current_set_id).state)
        assert recomputed > 0
        assert hass.states.get(active_id).state == "on"
        assert hass.states.get(state_id).state != STATE_DISABLED

        # Actions should fire for the resume/adjustment transition
        assert len(calls) > 0
        set_calls = [c for c in calls if c.data["entity_id"] == SET_CURRENT_SCRIPT]
        assert len(set_calls) >= 1


# ---------------------------------------------------------------------------
# Scenario 11: Changing max charger current during fallback
# ---------------------------------------------------------------------------


class TestParameterChangeDuringFallback:
    """User changes max charger current while the meter is unavailable and fallback is active.

    Verifies that the fallback current respects the new max charger limit
    when the meter recovers, and that the parameter change is correctly
    tracked while in fallback mode.
    """

    async def test_lower_max_during_set_current_fallback(
        self, hass: HomeAssistant,
    ) -> None:
        """Lowering max charger current below fallback causes the next meter recovery to respect the new limit."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_POWER_METER_ENTITY: POWER_METER,
                CONF_VOLTAGE: 230.0,
                CONF_MAX_SERVICE_CURRENT: 32.0,
                CONF_UNAVAILABLE_BEHAVIOR: UNAVAILABLE_BEHAVIOR_SET_CURRENT,
                CONF_UNAVAILABLE_FALLBACK_CURRENT: 10.0,
            },
            title="EV Load Balancing",
        )
        await setup_integration(hass, entry)

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")
        active_id = get_entity_id(hass, entry, "binary_sensor", "active")
        max_current_id = get_entity_id(hass, entry, "number", "max_charger_current")
        fallback_active_id = get_entity_id(hass, entry, "binary_sensor", "fallback_active")

        # Phase 1: Normal charging at 18 A (3000 W)
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 18.0

        # Phase 2: Meter goes unavailable → fallback to 10 A
        hass.states.async_set(POWER_METER, "unavailable")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 10.0
        assert hass.states.get(active_id).state == "on"
        assert hass.states.get(fallback_active_id).state == "on"

        # Phase 3: Lower max charger current to 8 A while in fallback
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": max_current_id, "value": 8.0},
            blocking=True,
        )
        await hass.async_block_till_done()

        # Parameter change while meter unavailable → coordinator tracks it
        assert hass.states.get(fallback_active_id).state == "on"

        # Phase 4: Meter recovers → normal computation with new max = 8 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        recovered = float(hass.states.get(current_set_id).state)
        assert recovered <= 8.0  # Capped at new max charger current
        assert hass.states.get(fallback_active_id).state == "off"

    async def test_lower_max_during_stop_fallback(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry,
    ) -> None:
        """Lowering max charger current during stop-mode fallback takes effect when meter recovers."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0  # Disable cooldown for clean transitions

        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")
        max_current_id = get_entity_id(hass, mock_config_entry, "number", "max_charger_current")
        active_id = get_entity_id(hass, mock_config_entry, "binary_sensor", "active")

        # Phase 1: Normal charging at 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 18.0

        # Phase 2: Meter unavailable → stop mode → 0 A
        hass.states.async_set(POWER_METER, "unavailable")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(active_id).state == "off"

        # Phase 3: Lower max charger current to 10 A while stopped
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": max_current_id, "value": 10.0},
            blocking=True,
        )
        await hass.async_block_till_done()

        # Phase 4: Meter recovers → charging resumes with new max = 10 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        recovered = float(hass.states.get(current_set_id).state)
        assert recovered > 0
        assert recovered <= 10.0  # New max
        assert hass.states.get(active_id).state == "on"


# ---------------------------------------------------------------------------
# Scenario 12: Ramp-up with custom timing boundaries
# ---------------------------------------------------------------------------


class TestRampUpCustomTiming:
    """Verify ramp-up cooldown with a non-default time value.

    Confirms the cooldown mechanism uses the configured ramp-up time
    rather than a hardcoded value, by testing with a 60-second cooldown.
    """

    async def test_sixty_second_ramp_up_blocks_then_releases(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """A 60-second ramp-up cooldown correctly blocks increases at 59s and allows them at 61s."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 60.0  # Non-default 60s cooldown

        mock_time = 2000.0

        def fake_monotonic():
            return mock_time

        coordinator._time_fn = fake_monotonic

        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")

        # Phase 1: Start charging at 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 18.0

        # Phase 2: Load spike → reduction at t=2001
        mock_time = 2001.0
        hass.states.async_set(POWER_METER, "8000")
        await hass.async_block_till_done()

        reduced = float(hass.states.get(current_set_id).state)
        assert reduced < 18.0

        # Phase 3: Load drops at t=2060 (59s after reduction) → still within 60s → held
        mock_time = 2060.0
        hass.states.async_set(POWER_METER, "3001")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == reduced  # Still held

        # Phase 4: At t=2062 (61s after reduction) → past 60s cooldown → increase allowed
        mock_time = 2062.0
        hass.states.async_set(POWER_METER, "3002")
        await hass.async_block_till_done()

        after_cooldown = float(hass.states.get(current_set_id).state)
        assert after_cooldown > reduced  # Increase now allowed


# ---------------------------------------------------------------------------
# Scenario 13: Disable during overload, re-enable after load drops
# ---------------------------------------------------------------------------


class TestDisableDuringOverloadAndReenable:
    """User disables load balancing during an overload, then re-enables when safe.

    Verifies the charger stays in its overloaded state while disabled,
    and that re-enabling triggers a fresh recompute from the current
    (now-safe) meter value.
    """

    async def test_disable_during_overload_reenable_when_safe(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """Charger stays stopped while disabled during overload, then resumes correctly on re-enable."""
        calls = async_mock_service(hass, "script", "turn_on")
        await setup_integration(hass, mock_config_entry_with_actions)
        coordinator = hass.data[DOMAIN][mock_config_entry_with_actions.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0  # Disable cooldown

        current_set_id = get_entity_id(hass, mock_config_entry_with_actions, "sensor", "current_set")
        active_id = get_entity_id(hass, mock_config_entry_with_actions, "binary_sensor", "active")
        switch_id = get_entity_id(hass, mock_config_entry_with_actions, "switch", "enabled")
        state_id = get_entity_id(hass, mock_config_entry_with_actions, "sensor", "balancer_state")

        # Phase 1: Start charging
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 18.0

        # Phase 2: Overload → stop
        hass.states.async_set(POWER_METER, "14000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(active_id).state == "off"

        # Phase 3: Disable load balancing while stopped
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": switch_id}, blocking=True
        )
        await hass.async_block_till_done()

        calls.clear()

        # Phase 4: Load drops while disabled → no action
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        assert len(calls) == 0  # Nothing fires while disabled
        assert hass.states.get(state_id).state == STATE_DISABLED

        # Phase 5: Re-enable → should immediately recompute and resume
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": switch_id}, blocking=True
        )
        await hass.async_block_till_done()

        recomputed = float(hass.states.get(current_set_id).state)
        assert recomputed > 0
        assert hass.states.get(active_id).state == "on"
        assert hass.states.get(state_id).state != STATE_DISABLED

        # start_charging + set_current should fire for the resume
        start_calls = [c for c in calls if c.data["entity_id"] == START_CHARGING_SCRIPT]
        set_calls = [c for c in calls if c.data["entity_id"] == SET_CURRENT_SCRIPT]
        assert len(start_calls) >= 1
        assert len(set_calls) >= 1


# ---------------------------------------------------------------------------
# Scenario 14: Min EV current change during fallback
# ---------------------------------------------------------------------------


class TestMinEvCurrentChangeDuringFallback:
    """User raises the min EV current threshold while the meter is in fallback.

    Verifies that when the meter recovers, the new min EV current is respected
    and charging only starts if the available headroom exceeds the new threshold.
    """

    async def test_raise_min_ev_during_fallback_affects_recovery(
        self, hass: HomeAssistant,
    ) -> None:
        """Raising min EV current during fallback causes charging to stop on recovery if headroom is insufficient."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_POWER_METER_ENTITY: POWER_METER,
                CONF_VOLTAGE: 230.0,
                CONF_MAX_SERVICE_CURRENT: 32.0,
                CONF_UNAVAILABLE_BEHAVIOR: UNAVAILABLE_BEHAVIOR_SET_CURRENT,
                CONF_UNAVAILABLE_FALLBACK_CURRENT: 10.0,
            },
            title="EV Load Balancing",
        )
        await setup_integration(hass, entry)
        coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0  # Disable cooldown

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")
        active_id = get_entity_id(hass, entry, "binary_sensor", "active")
        min_current_id = get_entity_id(hass, entry, "number", "min_ev_current")

        # Phase 1: Normal charging at 8 A
        # 5520 W at 230 V → available = 32 - 24 = 8 A
        hass.states.async_set(POWER_METER, "5520")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 8.0

        # Phase 2: Meter unavailable → fallback to 10 A
        hass.states.async_set(POWER_METER, "unavailable")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 10.0

        # Phase 3: Raise min EV current to 20 A during fallback
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": min_current_id, "value": 20.0},
            blocking=True,
        )
        await hass.async_block_till_done()

        # Phase 4: Meter recovers at high load (7000 W → available = 32 - 30.4 = 1.6 A)
        # raw_target = 10 + 1.6 = 11.6 → clamped to 11 A → below min (20 A) → stop
        hass.states.async_set(POWER_METER, "7000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0  # Below new min
        assert hass.states.get(active_id).state == "off"
