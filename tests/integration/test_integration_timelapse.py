"""Integration tests for realistic multi-step charging timelapse scenarios.

Tests exercise complete charging sessions with multiple transitions,
verifying the whole stack from power-meter events through to entity state
and action execution at every step.

Key design notes
----------------
* ``setup_integration`` pre-sets the meter to ``"0"`` before coordinator
  startup.  Re-setting the meter to ``"0"`` in a test does **not** fire a
  state-change event (HA deduplicates identical values).  Tests that need
  to fire an initial event therefore use a distinct value such as ``"100"``.
* ``resolve_balancer_state`` returns ``"stopped"`` whenever the charger
  current is 0 A (``active=False``), even when ``ramp_up_held=True``.
  ``"ramp_up_hold"`` is only returned while the charger is actively running
  (current > 0) and an increase is blocked by the cooldown.

Scenarios:
- Full timelapse: idle → start → partial overload → full stop → ramp-up
  still-stopped → resume → secondary reduction → second ramp-up hold → max
- Transient spike: brief overload reduces then clears; ramp-up blocks recovery
- Two consecutive spikes each reset the ramp-up timer
- Oscillating load: repeated reductions each reset the ramp-up timer;
  increase only allowed after a stable period
- Oscillation that never hits min_ev always keeps the charger active
- Stop by insufficient headroom and recovery at min_ev
- Exact one-amp below/at min_ev boundary
- Charger status sensor transition mid-session (Charging → Available)
"""

from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.ev_lb.const import (
    CONF_ACTION_SET_CURRENT,
    CONF_ACTION_START_CHARGING,
    CONF_ACTION_STOP_CHARGING,
    CONF_CHARGER_STATUS_ENTITY,
    CONF_MAX_SERVICE_CURRENT,
    CONF_POWER_METER_ENTITY,
    CONF_VOLTAGE,
    DOMAIN,
    STATE_ADJUSTING,
    STATE_RAMP_UP_HOLD,
    STATE_STOPPED,
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
# Helpers
# ---------------------------------------------------------------------------


def _meter_w(non_ev_a: float, ev_a: float, voltage: float = 230.0) -> str:
    """Return the total meter reading in Watts for given non-EV and EV loads.

    Produces the exact service draw seen by the meter:
    ``(non_ev_a + ev_a) * voltage``.
    """
    return str(round((non_ev_a + ev_a) * voltage, 2))


def _meter_for_available(
    desired_available_a: float,
    current_set_a: float,
    max_service_a: float = 32.0,
    voltage: float = 230.0,
) -> str:
    """Return the meter reading (Watts) that produces a target available_a.

    Inverts the load-balancer formula::

        available = max_service - non_ev
        non_ev    = max_service - desired_available
        service   = non_ev + current_set
        meter_w   = service * voltage
    """
    non_ev_a = max_service_a - desired_available_a
    service_current_a = non_ev_a + current_set_a
    return str(round(service_current_a * voltage, 2))


# ---------------------------------------------------------------------------
# Full 12-step timelapse
# ---------------------------------------------------------------------------


class TestFullChargingTimelapse:
    """Walk through a complete realistic EV charging session.

    The session covers every major state the balancer can enter:

    1.  Idle → start charging at max charger current (16 A)
    2.  Small overload → partial current reduction (16 → 12 A)
    3.  Larger overload → charging stops completely (12 → 0 A)
    4.  Load still above service limit → stays stopped
    5.  Load eases but ramp-up cooldown still active → still stopped
        (``"stopped"`` state, not ``"ramp_up_hold"`` — charger is at 0 A)
    6.  Ramp-up cooldown expires → charging resumes (0 → 8 A)
    7.  Load drops further → charger increases to max (8 → 16 A)
    8.  Secondary load spike → second reduction (16 → 14 A)
    9.  Load eases within new cooldown → increase blocked (``"ramp_up_hold"``
        — charger is running at 14 A, an *increase* is blocked)
    10. Second cooldown expires → charger returns to max (14 → 16 A)

    Uses a 16 A charger maximum so intermediate reductions are visible
    between the 6 A minimum and the 16 A maximum.

    Note: when the charger is at 0 A (stopped) and ramp-up would prevent
    a restart, ``balancer_state`` is ``"stopped"``, not ``"ramp_up_hold"``.
    The ``"ramp_up_hold"`` state only appears when current > 0 A and an
    *increase* is blocked by the cooldown.
    """

    async def test_full_twelve_step_timelapse(
        self, hass: HomeAssistant
    ) -> None:
        """Charger navigates idle→start→overload→stop→still-stopped→resume→secondary reduction→resume."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_POWER_METER_ENTITY: POWER_METER,
                CONF_VOLTAGE: 230.0,
                CONF_MAX_SERVICE_CURRENT: 32.0,
            },
            title="EV Timelapse",
        )
        await setup_integration(hass, entry)
        coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        coordinator.ramp_up_time_s = 30.0
        coordinator.max_charger_current = 16.0

        mock_time = 1000.0

        def fake_monotonic():
            return mock_time

        coordinator._time_fn = fake_monotonic

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")
        active_id = get_entity_id(hass, entry, "binary_sensor", "active")
        state_id = get_entity_id(hass, entry, "sensor", "balancer_state")

        # -------------------------------------------------------------------
        # Phase 1 (steps 1-2): Idle → start charging at 16 A (max)
        # setup_integration pre-sets meter to "0", so we use "100" to trigger a state change.
        # 100 W → service = 0.43 A → available = 31.6 A → capped at max_charger = 16 A
        # -------------------------------------------------------------------
        mock_time = 1000.0
        hass.states.async_set(POWER_METER, "100")  # "0"→"100" triggers a state change
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 16.0
        assert hass.states.get(active_id).state == "on"
        assert hass.states.get(state_id).state == STATE_ADJUSTING

        # -------------------------------------------------------------------
        # Phase 2 (steps 3-4): Small overload → partial reduction to 12 A
        # desired available = 12 A → meter = (32-12+16)*230 = 8280 W
        # -------------------------------------------------------------------
        mock_time = 1010.0
        hass.states.async_set(POWER_METER, _meter_for_available(12.0, 16.0))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 12.0
        assert hass.states.get(active_id).state == "on"
        assert hass.states.get(state_id).state == STATE_ADJUSTING

        # -------------------------------------------------------------------
        # Phase 3 (steps 5-6): Larger overload (available = 4 A < min_ev 6 A) → stop
        # meter = (32-4+12)*230 = 9200 W
        # -------------------------------------------------------------------
        mock_time = 1020.0
        hass.states.async_set(POWER_METER, _meter_for_available(4.0, 12.0))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(active_id).state == "off"
        assert hass.states.get(state_id).state == STATE_STOPPED

        # -------------------------------------------------------------------
        # Phase 4 (step 7): Load eases to 1 A above service limit — stays stopped
        # available = -1 A (non_ev = 33 A) → target = None → 0 A
        # -------------------------------------------------------------------
        mock_time = 1025.0
        hass.states.async_set(POWER_METER, _meter_for_available(-1.0, 0.0))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(active_id).state == "off"
        assert coordinator.available_current_a < 0

        # -------------------------------------------------------------------
        # Phase 5 (step 8): Load eases enough for 8 A, but ramp-up cooldown active
        # target = 8 A, elapsed = 20 s < 30 s → HOLD at 0 A
        # Since charger is at 0 A (not running), balancer_state = "stopped"
        # (ramp_up_hold only shows when charger is actively running and an
        # *increase* is blocked; here the charger is already stopped)
        # -------------------------------------------------------------------
        mock_time = 1040.0  # 20 s since last reduction at T=1020
        hass.states.async_set(POWER_METER, _meter_for_available(8.0, 0.0))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0  # held
        assert hass.states.get(active_id).state == "off"
        assert hass.states.get(state_id).state == STATE_STOPPED

        # -------------------------------------------------------------------
        # Phase 6 (step 9): Ramp-up cooldown expires → charging resumes
        # elapsed = 31 s > 30 s → increase allowed → 8 A
        # Slightly different meter (8.01 A) to trigger a new event
        # -------------------------------------------------------------------
        mock_time = 1051.0  # 31 s since T=1020 — cooldown cleared
        hass.states.async_set(POWER_METER, _meter_for_available(8.01, 0.0))
        await hass.async_block_till_done()

        resumed = float(hass.states.get(current_set_id).state)
        assert resumed >= 6.0  # Back above min_ev
        assert hass.states.get(active_id).state == "on"
        assert hass.states.get(state_id).state == STATE_ADJUSTING

        # -------------------------------------------------------------------
        # Phase 7 (step 10): Load drops — charger increases to max
        # available = 24 A → target = 16 A (cap at max_charger)
        # elapsed still > 30 s from T=1020 → increase allowed
        # -------------------------------------------------------------------
        mock_time = 1060.0
        hass.states.async_set(POWER_METER, _meter_for_available(24.0, resumed))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 16.0
        assert hass.states.get(state_id).state == STATE_ADJUSTING

        # -------------------------------------------------------------------
        # Phase 8 (step 11): Secondary spike — available = 14 A → reduce to 14 A
        # Records new last_reduction_time = T=1070
        # -------------------------------------------------------------------
        mock_time = 1070.0
        hass.states.async_set(POWER_METER, _meter_for_available(14.0, 16.0))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 14.0
        assert hass.states.get(state_id).state == STATE_ADJUSTING

        # -------------------------------------------------------------------
        # Phase 9 (step 12a): Load eases — target = 16 A (max), but new cooldown
        # Charger is RUNNING at 14 A (active=True) and an *increase* is blocked
        # → balancer_state = "ramp_up_hold"
        # elapsed = 5 s < 30 s since T=1070
        # -------------------------------------------------------------------
        mock_time = 1075.0
        hass.states.async_set(POWER_METER, _meter_for_available(24.0, 14.0))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 14.0  # held
        assert hass.states.get(active_id).state == "on"
        assert hass.states.get(state_id).state == STATE_RAMP_UP_HOLD  # running but blocked

        # -------------------------------------------------------------------
        # Phase 10 (step 12b): Second ramp-up expires → charger at max
        # elapsed = 31 s > 30 s since T=1070
        # -------------------------------------------------------------------
        mock_time = 1101.0
        hass.states.async_set(POWER_METER, _meter_for_available(24.01, 14.0))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 16.0
        assert hass.states.get(active_id).state == "on"
        assert hass.states.get(state_id).state == STATE_ADJUSTING


# ---------------------------------------------------------------------------
# Transient load spike
# ---------------------------------------------------------------------------


class TestTransientLoadSpike:
    """A brief overload spike causes a reduction; ramp-up blocks immediate recovery.

    When the load briefly spikes and then clears, the balancer reduces the
    charger current for safety.  The ramp-up cooldown prevents an immediate
    bounce-back, avoiding oscillation.  Only after the cooldown expires
    (30 s by default) can the current increase again.
    """

    async def test_brief_spike_reduces_then_ramp_up_holds_recovery(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Brief spike causes reduction; charger is held at reduced current until cooldown expires."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator.ramp_up_time_s = 30.0

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

        # Phase 2: Spike — available drops to 10 A → reduce to 10 A
        mock_time = 1010.0
        hass.states.async_set(POWER_METER, _meter_for_available(10.0, 18.0))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 10.0
        assert hass.states.get(state_id).state == STATE_ADJUSTING

        # Phase 3: Spike clears — available = 25 A, but 1 s since reduction → held
        # Charger is running at 10 A (active) and an increase is blocked → ramp_up_hold
        mock_time = 1011.0
        hass.states.async_set(POWER_METER, _meter_for_available(25.0, 10.0))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 10.0
        assert hass.states.get(state_id).state == STATE_RAMP_UP_HOLD

        # Phase 4: Still within cooldown at 20 s — still held
        mock_time = 1030.0
        hass.states.async_set(POWER_METER, _meter_for_available(25.01, 10.0))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 10.0
        assert hass.states.get(state_id).state == STATE_RAMP_UP_HOLD

        # Phase 5: Cooldown expires at 31 s → increase allowed
        mock_time = 1041.0  # 31 s after T=1010
        hass.states.async_set(POWER_METER, _meter_for_available(25.02, 10.0))
        await hass.async_block_till_done()

        final_current = float(hass.states.get(current_set_id).state)
        assert final_current > 10.0  # Increased after cooldown
        assert hass.states.get(state_id).state == STATE_ADJUSTING

    async def test_two_consecutive_spikes_each_reset_ramp_up_timer(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Each new reduction resets the ramp-up timer; the hold is measured from the last reduction."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator.ramp_up_time_s = 30.0

        mock_time = 1000.0

        def fake_monotonic():
            return mock_time

        coordinator._time_fn = fake_monotonic

        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")
        state_id = get_entity_id(hass, mock_config_entry, "sensor", "balancer_state")

        # Phase 1: Start at 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0

        # Phase 2: First spike at T=1010 → reduce to 14 A
        mock_time = 1010.0
        hass.states.async_set(POWER_METER, _meter_for_available(14.0, 18.0))
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 14.0

        # Phase 3: Load eases at T=1035 (25 s from first spike) → increase blocked
        mock_time = 1035.0  # 25 s from T=1010 — within 30 s cooldown
        hass.states.async_set(POWER_METER, _meter_for_available(25.0, 14.0))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 14.0
        assert hass.states.get(state_id).state == STATE_RAMP_UP_HOLD

        # Phase 4: Second spike at T=1038 → reduce to 10 A → RESETS timer to T=1038
        mock_time = 1038.0
        hass.states.async_set(POWER_METER, _meter_for_available(10.0, 14.0))
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 10.0

        # Phase 5: At T=1060 (50 s from first spike, but only 22 s from second) → still blocked
        mock_time = 1060.0
        hass.states.async_set(POWER_METER, _meter_for_available(25.0, 10.0))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 10.0
        assert hass.states.get(state_id).state == STATE_RAMP_UP_HOLD  # timer reset to T=1038

        # Phase 6: At T=1069 (31 s from second spike) → now allowed
        mock_time = 1069.0
        hass.states.async_set(POWER_METER, _meter_for_available(25.01, 10.0))
        await hass.async_block_till_done()

        final_current = float(hass.states.get(current_set_id).state)
        assert final_current > 10.0
        assert hass.states.get(state_id).state == STATE_ADJUSTING


# ---------------------------------------------------------------------------
# Oscillating load
# ---------------------------------------------------------------------------


class TestOscillatingLoad:
    """Repeated load oscillations each trigger reductions and reset the ramp-up timer.

    When household load bounces repeatedly (e.g., appliances cycling), the
    balancer instantly reduces on each upswing but is blocked from increasing
    on each downswing.  The increase is only allowed after 30 s with no
    further reductions.
    """

    async def test_repeated_oscillations_then_stable_recovery(
        self, hass: HomeAssistant
    ) -> None:
        """Repeated reductions keep resetting the timer; increase only allowed after stable period."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_POWER_METER_ENTITY: POWER_METER,
                CONF_VOLTAGE: 230.0,
                CONF_MAX_SERVICE_CURRENT: 32.0,
            },
            title="EV Oscillation",
        )
        await setup_integration(hass, entry)
        coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        coordinator.ramp_up_time_s = 30.0
        coordinator.max_charger_current = 24.0

        mock_time = 1000.0

        def fake_monotonic():
            return mock_time

        coordinator._time_fn = fake_monotonic

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")
        state_id = get_entity_id(hass, entry, "sensor", "balancer_state")

        # Phase 1: Start at 24 A (max_charger)
        # setup_integration sets meter to "0"; use "100" to fire a distinct event
        mock_time = 1000.0
        hass.states.async_set(POWER_METER, "100")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 24.0

        # Phase 2: First oscillation up — T=1010, available=17 A → reduce to 17 A
        mock_time = 1010.0
        hass.states.async_set(POWER_METER, _meter_for_available(17.0, 24.0))
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 17.0
        assert hass.states.get(state_id).state == STATE_ADJUSTING

        # Phase 3: Oscillation down — T=1015, would increase, but blocked (5 s < 30 s)
        mock_time = 1015.0
        hass.states.async_set(POWER_METER, _meter_for_available(24.0, 17.0))
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 17.0
        assert hass.states.get(state_id).state == STATE_RAMP_UP_HOLD

        # Phase 4: Second oscillation up — T=1025, available=14 A → reduce to 14 A (resets timer)
        mock_time = 1025.0
        hass.states.async_set(POWER_METER, _meter_for_available(14.0, 17.0))
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 14.0
        assert hass.states.get(state_id).state == STATE_ADJUSTING

        # Phase 5: Oscillation down — T=1030, would increase, but blocked (5 s from T=1025)
        mock_time = 1030.0
        hass.states.async_set(POWER_METER, _meter_for_available(24.0, 14.0))
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 14.0
        assert hass.states.get(state_id).state == STATE_RAMP_UP_HOLD

        # Phase 6: Load stays low for 31 s from last reduction (T=1025+31=T=1056) → allowed
        mock_time = 1056.0
        hass.states.async_set(POWER_METER, _meter_for_available(24.01, 14.0))
        await hass.async_block_till_done()

        final = float(hass.states.get(current_set_id).state)
        assert final > 14.0
        assert hass.states.get(state_id).state == STATE_ADJUSTING

    async def test_oscillation_never_stops_if_always_above_min_ev(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Oscillating load that always stays above min_ev never stops the charger."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator.ramp_up_time_s = 30.0

        mock_time = 1000.0

        def fake_monotonic():
            return mock_time

        coordinator._time_fn = fake_monotonic

        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")
        active_id = get_entity_id(hass, mock_config_entry, "binary_sensor", "active")

        # Start at 18 A (no prior reduction)
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0

        # Oscillate: available drops to 8 A → 20 A → 6 A → 15 A
        # Each step keeps current ≥ min_ev (6 A) → charger always on
        for mock_time, available in [
            (1010.0, 8.0),
            (1015.0, 20.0),
            (1020.0, 6.0),
            (1025.0, 15.0),
        ]:
            current = float(hass.states.get(current_set_id).state)
            hass.states.async_set(POWER_METER, _meter_for_available(available, current))
            await hass.async_block_till_done()

            assert hass.states.get(active_id).state == "on", (
                f"Charger stopped at available={available} A — should stay above min_ev"
            )
            assert float(hass.states.get(current_set_id).state) >= 6.0


# ---------------------------------------------------------------------------
# Stop by insufficient headroom
# ---------------------------------------------------------------------------


class TestStopByInsufficientHeadroom:
    """Charging stops when available < min_ev and resumes when load eases.

    This is the fundamental safety behaviour: the charger is switched off
    rather than operated at an unsafe sub-minimum current.
    """

    async def test_stop_when_below_min_then_resume(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Charger stops when headroom < min_ev and resumes once headroom is sufficient."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator.ramp_up_time_s = 0.0  # Disable cooldown for clean transitions

        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")
        active_id = get_entity_id(hass, mock_config_entry, "binary_sensor", "active")
        available_id = get_entity_id(hass, mock_config_entry, "sensor", "available_current")

        # Phase 1: Start at 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0

        # Phase 2: Load rises — available = 4 A < min_ev (6 A) → stop
        hass.states.async_set(POWER_METER, _meter_for_available(4.0, 18.0))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(active_id).state == "off"
        assert float(hass.states.get(available_id).state) == 4.0

        # Phase 3: Deeper into overload
        hass.states.async_set(POWER_METER, _meter_for_available(-3.0, 0.0))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0
        assert float(hass.states.get(available_id).state) == -3.0

        # Phase 4: Load eases to available = 6 A (exactly at min_ev) → restart
        hass.states.async_set(POWER_METER, _meter_for_available(6.0, 0.0))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 6.0
        assert hass.states.get(active_id).state == "on"

        # Phase 5: More headroom → current increases
        hass.states.async_set(POWER_METER, _meter_for_available(20.0, 6.0))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 20.0
        assert hass.states.get(active_id).state == "on"

    async def test_stop_one_amp_below_min_restart_at_min(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Available exactly one amp below min_ev stops the charger; exactly at min restarts it."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator.ramp_up_time_s = 0.0

        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")
        active_id = get_entity_id(hass, mock_config_entry, "binary_sensor", "active")

        # Start charging
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        current = float(hass.states.get(current_set_id).state)
        assert current > 0.0

        # available = min_ev - 1 = 5 A → stop
        hass.states.async_set(POWER_METER, _meter_for_available(5.0, current))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(active_id).state == "off"

        # available = min_ev = 6 A → restart
        hass.states.async_set(POWER_METER, _meter_for_available(6.0, 0.0))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 6.0
        assert hass.states.get(active_id).state == "on"


# ---------------------------------------------------------------------------
# Charger status sensor mid-session transition
# ---------------------------------------------------------------------------


class TestChargerStatusSensorMidSession:
    """Charger status sensor transitions during active charging affect headroom.

    When the sensor transitions from ``Charging`` to ``Available`` (EV finished
    or paused), the coordinator must zero out the EV draw estimate on the next
    meter event.  This prevents the balancer from subtracting phantom EV draw
    from available headroom when the charger is physically idle.
    """

    async def test_sensor_transition_charging_to_idle_corrects_headroom(
        self, hass: HomeAssistant
    ) -> None:
        """When status changes from Charging to Available, headroom is from house-only load."""
        status_entity = "sensor.ocpp_status"
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_POWER_METER_ENTITY: POWER_METER,
                CONF_VOLTAGE: 230.0,
                CONF_MAX_SERVICE_CURRENT: 32.0,
                CONF_CHARGER_STATUS_ENTITY: status_entity,
            },
            title="EV Sensor Transition",
        )
        hass.states.async_set(POWER_METER, "0")
        hass.states.async_set(status_entity, "Available")  # EV not charging initially
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        coordinator.ramp_up_time_s = 0.0
        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")
        available_id = get_entity_id(hass, entry, "sensor", "available_current")

        # Phase 1: House-only load (5 A) with sensor=Available → EV starts charging
        # meter = 5*230 = 1150 W → ev_estimate=0, non_ev=5, available=27 → target=27 A
        hass.states.async_set(POWER_METER, _meter_w(5.0, 0.0))  # 1150 W
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 27.0

        # Phase 2: EV now actually drawing 27 A; sensor transitions to Charging
        # meter = (5+27)*230 = 7360 W, ev_estimate=27 → non_ev=32-27=5, available=27 → stable
        hass.states.async_set(status_entity, "Charging")
        hass.states.async_set(POWER_METER, _meter_w(5.0, 27.0))  # 7360 W
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 27.0  # stable

        # Phase 3: EV finishes — sensor back to Available, meter drops to house-only
        # meter = (5+0)*230 = 1150 W, ev_estimate=0 (sensor=Available)
        # non_ev = max(0, 5-0) = 5A, available = 27A → target = 27A (correct)
        # Without the sensor (ev_estimate=27): non_ev=max(0,5-27)=0, available=32A → WRONG!
        hass.states.async_set(status_entity, "Available")
        hass.states.async_set(POWER_METER, _meter_w(5.0, 0.0))  # back to 1150 W
        await hass.async_block_till_done()

        available_after = float(hass.states.get(available_id).state)
        target_after = float(hass.states.get(current_set_id).state)

        # available = 32 - 5 = 27 A (house-only; no phantom EV subtraction)
        assert abs(available_after - 27.0) < 0.5
        # target should be 27 A — NOT 32 A (which would happen without the sensor)
        assert target_after < 32.0
        assert abs(target_after - 27.0) < 1.0

    async def test_sensor_prevents_overshoot_when_ev_pauses_during_high_load(
        self, hass: HomeAssistant
    ) -> None:
        """When EV pauses (sensor=Available) during high house load, headroom is correctly reduced.

        Without the sensor, the coordinator would subtract the last commanded
        EV current from the (house-only) meter, making non_ev look near-zero
        and over-reporting available headroom.
        With the sensor=Available, ev_estimate=0 and the true house-only load
        is used, giving a much lower headroom estimate.
        """
        status_entity = "sensor.ocpp_status"
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_POWER_METER_ENTITY: POWER_METER,
                CONF_VOLTAGE: 230.0,
                CONF_MAX_SERVICE_CURRENT: 32.0,
                CONF_CHARGER_STATUS_ENTITY: status_entity,
            },
            title="EV Sensor Pause",
        )
        hass.states.async_set(POWER_METER, "0")
        hass.states.async_set(status_entity, "Charging")
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        coordinator.ramp_up_time_s = 0.0
        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")
        available_id = get_entity_id(hass, entry, "sensor", "available_current")

        # Phase 1: Start charging — 0 W → available = 32 A → target = 32 A
        # Use "0.0" (float string) to trigger a distinct event from the "0" initial state
        hass.states.async_set(POWER_METER, "0.0")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 32.0

        # Phase 2: EV pauses (sensor=Available), high house load of 25 A present
        # Meter = 25 A (house only, EV not drawing) = 5750 W
        # With sensor=Available: ev_estimate=0, non_ev=25, available=7 A → target=7 A
        hass.states.async_set(status_entity, "Available")
        hass.states.async_set(POWER_METER, _meter_w(25.0, 0.0))  # 5750 W
        await hass.async_block_till_done()

        available_with_sensor = float(hass.states.get(available_id).state)
        target_with_sensor = float(hass.states.get(current_set_id).state)

        # available = 32 - 25 = 7 A (no phantom 32 A EV subtraction)
        assert abs(available_with_sensor - 7.0) < 0.5
        assert target_with_sensor == 7.0
        # Without the sensor (sensor=Charging, ev_estimate=32):
        #   non_ev = max(0, 25-32) = 0 A → available = 32 A → target = 32 A  ← wrong!
        # The sensor correctly restricted the target to 7 A.

    async def test_full_cycle_with_sensor_charge_stop_resume(
        self, hass: HomeAssistant
    ) -> None:
        """Full cycle with sensor: start, overload stop, EV done, load drops, resume."""
        status_entity = "sensor.ocpp_status"
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_POWER_METER_ENTITY: POWER_METER,
                CONF_VOLTAGE: 230.0,
                CONF_MAX_SERVICE_CURRENT: 32.0,
                CONF_CHARGER_STATUS_ENTITY: status_entity,
            },
            title="EV Full Cycle",
        )
        hass.states.async_set(POWER_METER, "0")
        hass.states.async_set(status_entity, "Charging")
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        coordinator.ramp_up_time_s = 0.0
        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")
        active_id = get_entity_id(hass, entry, "binary_sensor", "active")

        # Phase 1: House-only load (13 A = 2990 W) → EV starts charging at 19 A
        # ev_estimate=0, non_ev=13, available=19 A → target=19 A
        hass.states.async_set(POWER_METER, _meter_w(13.0, 0.0))  # 2990 W
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 19.0
        assert hass.states.get(active_id).state == "on"

        # Phase 2: Overload — available < min_ev → stop
        hass.states.async_set(POWER_METER, _meter_for_available(4.0, 19.0))
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(active_id).state == "off"

        # Phase 3: EV finishes (sensor=Available), house load drops to 5 A
        hass.states.async_set(status_entity, "Available")
        hass.states.async_set(POWER_METER, _meter_for_available(27.0, 0.0))  # 5 A house
        await hass.async_block_till_done()

        # With sensor=Available: ev_estimate=0, available=27 A → target=27 A
        assert float(hass.states.get(current_set_id).state) == 27.0
        assert hass.states.get(active_id).state == "on"


# ---------------------------------------------------------------------------
# Stop-and-spike combination: overload fires, partial recovery, second spike
# ---------------------------------------------------------------------------


class TestOverloadWithSpikesAndRecovery:
    """Stop → ramp-up still-stopped → second spike → final recovery.

    When the charger has stopped (0 A) due to overload, load eases partially
    but the ramp-up cooldown prevents restart (state remains ``"stopped"`` —
    since the charger is at 0 A, *not* ``"ramp_up_hold"`` which only occurs
    when the charger is running and an increase is blocked).  A second spike
    while still in the hold period does not break the state.  Final recovery
    happens only after the cooldown fully expires.
    """

    async def test_stop_hold_second_spike_and_final_resume_with_actions(
        self, hass: HomeAssistant
    ) -> None:
        """Stop → stopped-during-hold → second spike → final resume with correct actions."""
        calls = async_mock_service(hass, "script", "turn_on")

        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_POWER_METER_ENTITY: POWER_METER,
                CONF_VOLTAGE: 230.0,
                CONF_MAX_SERVICE_CURRENT: 32.0,
                CONF_ACTION_SET_CURRENT: SET_CURRENT_SCRIPT,
                CONF_ACTION_STOP_CHARGING: STOP_CHARGING_SCRIPT,
                CONF_ACTION_START_CHARGING: START_CHARGING_SCRIPT,
            },
            title="EV Spike Test",
        )
        await setup_integration(hass, entry)
        coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        coordinator.ramp_up_time_s = 30.0

        mock_time = 1000.0

        def fake_monotonic():
            return mock_time

        coordinator._time_fn = fake_monotonic

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")
        active_id = get_entity_id(hass, entry, "binary_sensor", "active")
        state_id = get_entity_id(hass, entry, "sensor", "balancer_state")

        # Phase 1: Start at 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0
        calls.clear()

        # Phase 2: Massive overload → stop
        mock_time = 1010.0
        hass.states.async_set(POWER_METER, _meter_for_available(-8.0, 18.0))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(active_id).state == "off"
        stop_calls = [c for c in calls if c.data["entity_id"] == STOP_CHARGING_SCRIPT]
        assert len(stop_calls) == 1
        calls.clear()

        # Phase 3: Load eases (available = 20 A) but within ramp-up cooldown (15 s)
        # Charger is at 0 A — state = "stopped" (not "ramp_up_hold")
        mock_time = 1025.0
        hass.states.async_set(POWER_METER, _meter_for_available(20.0, 0.0))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(state_id).state == STATE_STOPPED  # at 0 A → stopped
        assert len(calls) == 0  # No action while held

        # Phase 4: Second spike while still in hold period
        mock_time = 1028.0
        hass.states.async_set(POWER_METER, _meter_for_available(-3.0, 0.0))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0
        assert coordinator.available_current_a < 0

        # Phase 5: Ramp-up expires (31 s from original stop at T=1010) → resume
        mock_time = 1041.0
        hass.states.async_set(POWER_METER, _meter_for_available(18.0, 0.0))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 18.0
        assert hass.states.get(active_id).state == "on"
        start_calls = [c for c in calls if c.data["entity_id"] == START_CHARGING_SCRIPT]
        assert len(start_calls) == 1
