"""Balancing coordinator for EV Charger Load Balancing.

Subscribes to the configured power-meter entity and, on every state
change, recomputes the target charging current using the pure functions
in :mod:`load_balancer`.  Entity state is updated via the HA dispatcher
so sensor/binary-sensor platforms can refresh without tight coupling.
"""

from __future__ import annotations

import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_MAX_SERVICE_CURRENT,
    CONF_POWER_METER_ENTITY,
    CONF_UNAVAILABLE_BEHAVIOR,
    CONF_UNAVAILABLE_FALLBACK_CURRENT,
    CONF_VOLTAGE,
    DEFAULT_MAX_CHARGER_CURRENT,
    DEFAULT_MIN_EV_CURRENT,
    DEFAULT_RAMP_UP_TIME,
    DEFAULT_UNAVAILABLE_BEHAVIOR,
    DEFAULT_UNAVAILABLE_FALLBACK_CURRENT,
    SIGNAL_UPDATE_FMT,
    UNAVAILABLE_BEHAVIOR_IGNORE,
    UNAVAILABLE_BEHAVIOR_SET_CURRENT,
    UNAVAILABLE_BEHAVIOR_STOP,
)
from .load_balancer import apply_ramp_up_limit, clamp_current, compute_available_current

_LOGGER = logging.getLogger(__name__)


class EvLoadBalancerCoordinator:
    """Coordinate power-meter events and single-charger balancing logic.

    Listens for power-meter state changes, computes the target charging
    current, applies the ramp-up cooldown, and publishes the result via
    the HA dispatcher so entity platforms can update.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the coordinator from config entry data."""
        self.hass = hass
        self.entry = entry

        # Config entry values (immutable for the lifetime of this coordinator)
        self._voltage: float = entry.data[CONF_VOLTAGE]
        self._max_service_current: float = entry.data[CONF_MAX_SERVICE_CURRENT]
        self._power_meter_entity: str = entry.data[CONF_POWER_METER_ENTITY]
        self._unavailable_behavior: str = entry.data.get(
            CONF_UNAVAILABLE_BEHAVIOR,
            DEFAULT_UNAVAILABLE_BEHAVIOR,
        )
        self._unavailable_fallback_a: float = entry.data.get(
            CONF_UNAVAILABLE_FALLBACK_CURRENT,
            DEFAULT_UNAVAILABLE_FALLBACK_CURRENT,
        )

        # Runtime parameters (updated by number/switch entities)
        self.max_charger_current: float = DEFAULT_MAX_CHARGER_CURRENT
        self.min_ev_current: float = DEFAULT_MIN_EV_CURRENT
        self.enabled: bool = True

        # Computed state (read by sensor/binary-sensor entities)
        self.current_set_a: float = 0.0
        self.available_current_a: float = 0.0
        self.active: bool = False

        # Ramp-up cooldown tracking
        self._last_reduction_time: float | None = None
        self._ramp_up_time_s: float = DEFAULT_RAMP_UP_TIME
        self._time_fn = time.monotonic

        # Dispatcher signal name
        self.signal_update: str = SIGNAL_UPDATE_FMT.format(
            entry_id=entry.entry_id,
        )

        # Listener removal callback
        self._unsub_listener: callback | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @callback
    def async_start(self) -> None:
        """Start listening to power-meter state changes."""
        self._unsub_listener = async_track_state_change_event(
            self.hass,
            [self._power_meter_entity],
            self._handle_power_change,
        )
        _LOGGER.debug(
            "Coordinator started — listening to %s", self._power_meter_entity
        )

    @callback
    def async_stop(self) -> None:
        """Stop listening and clean up."""
        if self._unsub_listener is not None:
            self._unsub_listener()
            self._unsub_listener = None
        _LOGGER.debug("Coordinator stopped")

    # ------------------------------------------------------------------
    # Event handler
    # ------------------------------------------------------------------

    @callback
    def _handle_power_change(self, event: Event) -> None:
        """React to a power-meter state change and recompute the target."""
        if not self.enabled:
            return

        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (
            "unavailable",
            "unknown",
        ):
            self._apply_fallback_current()
            return

        try:
            house_power_w = float(new_state.state)
        except (ValueError, TypeError):
            _LOGGER.warning(
                "Could not parse power meter value: %s", new_state.state
            )
            return

        self._recompute(house_power_w)

    # ------------------------------------------------------------------
    # On-demand recompute (triggered by number/switch changes)
    # ------------------------------------------------------------------

    @callback
    def async_recompute_from_current_state(self) -> None:
        """Re-run the balancing algorithm using the last known power meter value.

        Called when a runtime parameter changes (max charger current,
        min EV current, or the enabled switch) so the new value takes
        effect immediately without waiting for the next power-meter event.
        """
        if not self.enabled:
            return

        state = self.hass.states.get(self._power_meter_entity)
        if state is None or state.state in ("unavailable", "unknown"):
            return

        try:
            house_power_w = float(state.state)
        except (ValueError, TypeError):
            return

        self._recompute(house_power_w)

    # ------------------------------------------------------------------
    # Fallback for unavailable power meter
    # ------------------------------------------------------------------

    def _apply_fallback_current(self) -> None:
        """Handle the power meter becoming unavailable or unknown.

        Behavior depends on the configured ``unavailable_behavior``:

        * **ignore** — do nothing; keep the last computed values.
        * **stop** — set charger current to 0 A (safest).
        * **set_current** — apply the configured fallback current, capped
          at the charger maximum so it never exceeds the physical limit.
        """
        behavior = self._unavailable_behavior

        if behavior == UNAVAILABLE_BEHAVIOR_IGNORE:
            _LOGGER.info(
                "Power meter %s is unavailable — ignoring (keeping last value %.1f A)",
                self._power_meter_entity,
                self.current_set_a,
            )
            return

        if behavior == UNAVAILABLE_BEHAVIOR_SET_CURRENT:
            # Cap at the charger maximum so the fallback never exceeds the
            # physical charger limit, even if the configured value is higher.
            fallback = min(self._unavailable_fallback_a, self.max_charger_current)
            _LOGGER.warning(
                "Power meter %s is unavailable — applying fallback current %.1f A "
                "(configured %.1f A, capped to max charger current %.1f A)",
                self._power_meter_entity,
                fallback,
                self._unavailable_fallback_a,
                self.max_charger_current,
            )
        else:
            # Default: stop charging
            fallback = 0.0
            _LOGGER.warning(
                "Power meter %s is unavailable — stopping charging (0 A)",
                self._power_meter_entity,
            )

        # Without a valid meter reading, headroom is unknown — report 0 A
        # as available and apply the determined fallback for the charger.
        self.available_current_a = 0.0
        self.current_set_a = fallback
        self.active = fallback > 0

        async_dispatcher_send(self.hass, self.signal_update)

    # ------------------------------------------------------------------
    # Core computation
    # ------------------------------------------------------------------

    def _recompute(self, house_power_w: float) -> None:
        """Run the single-charger balancing algorithm and publish updates."""
        available_a = compute_available_current(
            house_power_w,
            self._max_service_current,
            self._voltage,
        )

        # Target = current charging current + headroom
        raw_target_a = self.current_set_a + available_a

        # Clamp to charger limits
        clamped = clamp_current(
            raw_target_a,
            self.max_charger_current,
            self.min_ev_current,
        )
        target_a = 0.0 if clamped is None else clamped

        # Apply ramp-up limit (instant down, delayed up)
        now = self._time_fn()
        final_a = apply_ramp_up_limit(
            self.current_set_a,
            target_a,
            self._last_reduction_time,
            now,
            self._ramp_up_time_s,
        )

        # Track reductions for ramp-up cooldown
        if final_a < self.current_set_a:
            self._last_reduction_time = now

        # Update computed state
        self.available_current_a = round(available_a, 2)
        self.current_set_a = final_a
        self.active = final_a > 0

        # Notify entities
        async_dispatcher_send(self.hass, self.signal_update)
