"""Integration test: full 12-step charging timelapse.

Exercises the complete realistic EV charging session from idle through
partial overloads, a full stop, ramp-up hold, resumption, and a secondary
reduction with a second cooldown expiry.

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
"""

from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ev_lb.const import (
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
    meter_for_available,
    setup_integration,
    get_entity_id,
)


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
        hass.states.async_set(POWER_METER, meter_for_available(12.0, 16.0))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 12.0
        assert hass.states.get(active_id).state == "on"
        assert hass.states.get(state_id).state == STATE_ADJUSTING

        # -------------------------------------------------------------------
        # Phase 3 (steps 5-6): Larger overload (available = 4 A < min_ev 6 A) → stop
        # meter = (32-4+12)*230 = 9200 W
        # -------------------------------------------------------------------
        mock_time = 1020.0
        hass.states.async_set(POWER_METER, meter_for_available(4.0, 12.0))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(active_id).state == "off"
        assert hass.states.get(state_id).state == STATE_STOPPED

        # -------------------------------------------------------------------
        # Phase 4 (step 7): Load eases to 1 A above service limit — stays stopped
        # available = -1 A (non_ev = 33 A) → target = None → 0 A
        # -------------------------------------------------------------------
        mock_time = 1025.0
        hass.states.async_set(POWER_METER, meter_for_available(-1.0, 0.0))
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
        hass.states.async_set(POWER_METER, meter_for_available(8.0, 0.0))
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
        hass.states.async_set(POWER_METER, meter_for_available(8.01, 0.0))
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
        hass.states.async_set(POWER_METER, meter_for_available(24.0, resumed))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 16.0
        assert hass.states.get(state_id).state == STATE_ADJUSTING

        # -------------------------------------------------------------------
        # Phase 8 (step 11): Secondary spike — available = 14 A → reduce to 14 A
        # Records new last_reduction_time = T=1070
        # -------------------------------------------------------------------
        mock_time = 1070.0
        hass.states.async_set(POWER_METER, meter_for_available(14.0, 16.0))
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
        hass.states.async_set(POWER_METER, meter_for_available(24.0, 14.0))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 14.0  # held
        assert hass.states.get(active_id).state == "on"
        assert hass.states.get(state_id).state == STATE_RAMP_UP_HOLD  # running but blocked

        # -------------------------------------------------------------------
        # Phase 10 (step 12b): Second ramp-up expires → charger at max
        # elapsed = 31 s > 30 s since T=1070
        # -------------------------------------------------------------------
        mock_time = 1101.0
        hass.states.async_set(POWER_METER, meter_for_available(24.01, 14.0))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 16.0
        assert hass.states.get(active_id).state == "on"
        assert hass.states.get(state_id).state == STATE_ADJUSTING


