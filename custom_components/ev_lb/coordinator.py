"""Balancing coordinator for EV Charger Load Balancing.

Subscribes to the configured power-meter entity and, on every state
change, recomputes the target charging current using the pure functions
in :mod:`load_balancer`.  Entity state is updated via the HA dispatcher
so sensor/binary-sensor platforms can refresh without tight coupling.

When action scripts are configured, the coordinator executes the
appropriate charger commands (set_current, stop_charging, start_charging)
on every state transition.
"""

from __future__ import annotations

import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event

from homeassistant.components.persistent_notification import (
    async_create as pn_async_create,
    async_dismiss as pn_async_dismiss,
)

from .const import (
    CONF_ACTION_SET_CURRENT,
    CONF_ACTION_START_CHARGING,
    CONF_ACTION_STOP_CHARGING,
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
    EVENT_ACTION_FAILED,
    EVENT_CHARGING_RESUMED,
    EVENT_FALLBACK_ACTIVATED,
    EVENT_METER_UNAVAILABLE,
    EVENT_OVERLOAD_STOP,
    NOTIFICATION_ACTION_FAILED_FMT,
    NOTIFICATION_FALLBACK_ACTIVATED_FMT,
    NOTIFICATION_METER_UNAVAILABLE_FMT,
    NOTIFICATION_OVERLOAD_STOP_FMT,
    REASON_FALLBACK_UNAVAILABLE,
    REASON_MANUAL_OVERRIDE,
    REASON_PARAMETER_CHANGE,
    REASON_POWER_METER_UPDATE,
    SAFETY_MAX_POWER_METER_W,
    SIGNAL_UPDATE_FMT,
    STATE_ACTIVE,
    STATE_ADJUSTING,
    STATE_DISABLED,
    STATE_RAMP_UP_HOLD,
    STATE_STOPPED,
    UNAVAILABLE_BEHAVIOR_IGNORE,
    UNAVAILABLE_BEHAVIOR_SET_CURRENT,
)
from .load_balancer import apply_ramp_up_limit, clamp_current, compute_available_current
from ._log import get_logger

_LOGGER = get_logger(__name__)


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

        self._init_action_scripts(entry)

        # Runtime parameters (updated by number/switch entities)
        self.max_charger_current: float = DEFAULT_MAX_CHARGER_CURRENT
        self.min_ev_current: float = DEFAULT_MIN_EV_CURRENT
        self.enabled: bool = True
        self.ramp_up_time_s: float = DEFAULT_RAMP_UP_TIME

        # Computed state (read by sensor/binary-sensor entities)
        self.current_set_a: float = 0.0
        self.available_current_a: float = 0.0
        self.active: bool = False
        self.last_action_reason: str = ""
        self.balancer_state: str = STATE_STOPPED
        self.meter_healthy: bool = True
        self.fallback_active: bool = False
        self.configured_fallback: str = self._unavailable_behavior

        # Ramp-up cooldown tracking
        self._last_reduction_time: float | None = None
        self._time_fn = time.monotonic

        # Dispatcher signal name
        self.signal_update: str = SIGNAL_UPDATE_FMT.format(
            entry_id=entry.entry_id,
        )

        # Listener removal callback
        self._unsub_listener: callback | None = None

    def _init_action_scripts(self, entry: ConfigEntry) -> None:
        """Load action script entity IDs from the config entry.

        Prefers options over data so changes via options flow take
        effect without deleting and re-creating the config entry.
        """
        self._action_set_current: str | None = entry.options.get(
            CONF_ACTION_SET_CURRENT,
            entry.data.get(CONF_ACTION_SET_CURRENT),
        )
        self._action_stop_charging: str | None = entry.options.get(
            CONF_ACTION_STOP_CHARGING,
            entry.data.get(CONF_ACTION_STOP_CHARGING),
        )
        self._action_start_charging: str | None = entry.options.get(
            CONF_ACTION_START_CHARGING,
            entry.data.get(CONF_ACTION_START_CHARGING),
        )

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
            "Coordinator started — listening to %s "
            "(voltage=%.0f V, service_limit=%.0f A, unavailable=%s)",
            self._power_meter_entity,
            self._voltage,
            self._max_service_current,
            self._unavailable_behavior,
        )

        # Sync meter health flags if meter is already unavailable at startup
        meter_state = self.hass.states.get(self._power_meter_entity)
        if meter_state is None or meter_state.state in ("unavailable", "unknown"):
            self.meter_healthy = False
            self.fallback_active = True

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
        new_state = event.data.get("new_state")
        is_unavailable = new_state is None or new_state.state in (
            "unavailable",
            "unknown",
        )

        # Always track meter health so diagnostic sensors stay accurate
        # even when load balancing is disabled.
        if is_unavailable:
            self.meter_healthy = False
            self.fallback_active = True
        else:
            self.meter_healthy = True
            self.fallback_active = False

        if not self.enabled:
            _LOGGER.debug("Power meter changed but load balancing is disabled — skipping")
            self.balancer_state = STATE_DISABLED
            async_dispatcher_send(self.hass, self.signal_update)
            return

        if is_unavailable:
            self._apply_fallback_current()
            return

        try:
            house_power_w = float(new_state.state)
        except (ValueError, TypeError):
            _LOGGER.warning(
                "Could not parse power meter value: %s", new_state.state
            )
            return

        if abs(house_power_w) > SAFETY_MAX_POWER_METER_W:
            _LOGGER.warning(
                "Power meter value %.0f W exceeds safety limit (%.0f W) "
                "— ignoring as likely sensor error",
                house_power_w,
                SAFETY_MAX_POWER_METER_W,
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
            _LOGGER.debug("Parameter changed but load balancing is disabled — skipping recompute")
            self.balancer_state = STATE_DISABLED
            async_dispatcher_send(self.hass, self.signal_update)
            return

        state = self.hass.states.get(self._power_meter_entity)
        if state is None or state.state in ("unavailable", "unknown"):
            _LOGGER.debug(
                "Parameter changed but power meter is %s — skipping recompute",
                state.state if state else "missing",
            )
            self.meter_healthy = False
            self.fallback_active = True
            async_dispatcher_send(self.hass, self.signal_update)
            return

        try:
            house_power_w = float(state.state)
        except (ValueError, TypeError):
            return

        if abs(house_power_w) > SAFETY_MAX_POWER_METER_W:
            _LOGGER.warning(
                "Power meter value %.0f W exceeds safety limit (%.0f W) "
                "— ignoring as likely sensor error",
                house_power_w,
                SAFETY_MAX_POWER_METER_W,
            )
            return

        _LOGGER.debug(
            "Runtime parameter changed — recomputing with last meter value %.1f W",
            house_power_w,
        )
        self._recompute(house_power_w, REASON_PARAMETER_CHANGE)

    # ------------------------------------------------------------------
    # Manual override via ev_lb.set_limit service
    # ------------------------------------------------------------------

    @callback
    def manual_set_limit(self, current_a: float) -> None:
        """Manually set the charger current, bypassing the balancing algorithm.

        The requested current is clamped to the charger's min/max limits.
        If the clamped value falls below the minimum EV current, charging
        is stopped (target set to 0 A).  The override is one-shot: the
        next power-meter event will resume normal automatic balancing.
        """
        clamped = clamp_current(
            current_a,
            self.max_charger_current,
            self.min_ev_current,
        )
        target = 0.0 if clamped is None else clamped
        _LOGGER.debug(
            "Manual override: requested=%.1f A, clamped=%.1f A",
            current_a,
            target,
        )
        self._update_and_notify(self.available_current_a, target, REASON_MANUAL_OVERRIDE)

    # ------------------------------------------------------------------
    # Fallback for unavailable power meter
    # ------------------------------------------------------------------

    def _apply_fallback_current(self) -> None:
        """Handle the power meter becoming unavailable or unknown.

        Resolves the appropriate fallback and applies it.  The "ignore"
        mode keeps the last computed value; all other modes update the
        charger current via ``_update_and_notify``.
        """
        self.meter_healthy = False
        self.fallback_active = True
        fallback = self._resolve_fallback()
        if fallback is None:
            # Ignore mode — keep last value, just update sensor state
            async_dispatcher_send(self.hass, self.signal_update)
            return
        self._update_and_notify(0.0, fallback, REASON_FALLBACK_UNAVAILABLE)

    def _resolve_fallback(self) -> float | None:
        """Determine the fallback current for an unavailable power meter.

        Returns ``None`` for ignore mode (caller should skip the update),
        ``0.0`` for stop mode, or the configured fallback capped at the
        charger maximum for set-current mode.
        """
        behavior = self._unavailable_behavior

        if behavior == UNAVAILABLE_BEHAVIOR_IGNORE:
            _LOGGER.debug(
                "Power meter %s is unavailable — ignoring (keeping last value %.1f A)",
                self._power_meter_entity,
                self.current_set_a,
            )
            return None

        if behavior == UNAVAILABLE_BEHAVIOR_SET_CURRENT:
            fallback = min(self._unavailable_fallback_a, self.max_charger_current)
            _LOGGER.warning(
                "Power meter %s is unavailable — applying fallback current %.1f A "
                "(configured %.1f A, capped to max charger current %.1f A)",
                self._power_meter_entity,
                fallback,
                self._unavailable_fallback_a,
                self.max_charger_current,
            )
            return fallback

        # Default: stop charging
        _LOGGER.warning(
            "Power meter %s is unavailable — stopping charging (0 A)",
            self._power_meter_entity,
        )
        return 0.0

    # ------------------------------------------------------------------
    # Core computation
    # ------------------------------------------------------------------

    def _recompute(self, house_power_w: float, reason: str = REASON_POWER_METER_UPDATE) -> None:
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
            self.ramp_up_time_s,
        )

        # Track reductions for ramp-up cooldown
        if final_a < self.current_set_a:
            self._last_reduction_time = now

        _LOGGER.debug(
            "Recompute (%s): house=%.0f W, available=%.1f A, "
            "raw_target=%.1f A, clamped=%.1f A, final=%.1f A",
            reason,
            house_power_w,
            available_a,
            raw_target_a,
            target_a,
            final_a,
        )

        if final_a != target_a:
            _LOGGER.debug(
                "Ramp-up cooldown holding current at %.1f A (target %.1f A)",
                final_a,
                target_a,
            )

        # Determine balancer operational state
        ramp_up_held = final_a != target_a and final_a < target_a

        # Update computed state and execute actions
        self._update_and_notify(round(available_a, 2), final_a, reason, ramp_up_held)

    # ------------------------------------------------------------------
    # State update, action execution, and entity notification
    # ------------------------------------------------------------------

    def _update_and_notify(
        self,
        available_a: float,
        current_a: float,
        reason: str = "",
        ramp_up_held: bool = False,
    ) -> None:
        """Update state, fire charger actions for transitions, and notify entities.

        Captures the previous state, applies the new values, schedules any
        required charger action calls, fires HA events for notable conditions,
        and sends the HA dispatcher signal so entity platforms can refresh.

        A defense-in-depth safety clamp ensures the output never exceeds
        the service or charger limits, even if upstream logic has a bug.
        """
        # Safety clamp: output must never exceed charger max or service limit
        if current_a > 0:
            safe_max = min(self.max_charger_current, self._max_service_current)
            if current_a > safe_max:
                _LOGGER.warning(
                    "Safety clamp: computed %.1f A exceeds safe maximum %.1f A "
                    "(charger_max=%.1f, service_max=%.1f) — clamping",
                    current_a,
                    safe_max,
                    self.max_charger_current,
                    self._max_service_current,
                )
                current_a = safe_max

        prev_active = self.active
        prev_current = self.current_set_a

        self.available_current_a = available_a
        self.current_set_a = current_a
        self.active = current_a > 0
        self.last_action_reason = reason

        # Determine balancer operational state
        self.balancer_state = self._resolve_balancer_state(
            prev_active, prev_current, ramp_up_held, reason,
        )

        # Log significant transitions at info level (low cadence)
        if not prev_active and self.active:
            _LOGGER.info("Charging started at %.1f A", current_a)
        elif prev_active and not self.active:
            _LOGGER.info("Charging stopped (was %.1f A, reason=%s)", prev_current, reason)

        # Fire HA events and manage persistent notifications
        self._fire_events(prev_active, prev_current, reason)

        # Schedule charger action execution for state transitions
        if self._has_actions():
            self.hass.async_create_task(
                self._execute_actions(prev_active, prev_current),
                eager_start=False,
            )

        async_dispatcher_send(self.hass, self.signal_update)

    def _resolve_balancer_state(
        self,
        prev_active: bool,
        prev_current: float,
        ramp_up_held: bool,
        reason: str,
    ) -> str:
        """Determine the balancer's operational state for the diagnostic sensor.

        Maps to the charger state transitions described in the README:
        - **disabled**: load balancing switch is off
        - **stopped**: charger target is 0 A
        - **ramp_up_hold**: increase blocked by cooldown
        - **adjusting**: current changed this cycle
        - **active**: target current > 0 and unchanged (steady state)

        Meter health and fallback status are tracked by separate sensors.
        """
        if not self.enabled:
            return STATE_DISABLED
        if not self.active:
            return STATE_STOPPED
        if ramp_up_held:
            return STATE_RAMP_UP_HOLD
        if self.current_set_a != prev_current or not prev_active:
            return STATE_ADJUSTING
        return STATE_ACTIVE

    # ------------------------------------------------------------------
    # Event notifications and persistent notifications
    # ------------------------------------------------------------------

    @callback
    def _fire_events(
        self, prev_active: bool, prev_current: float, reason: str
    ) -> None:
        """Fire HA events and manage persistent notifications for notable conditions.

        Delegates to focused helpers for each fault/resolution condition.
        """
        self._fire_fault_events(prev_active, prev_current, reason)
        self._fire_resolution_events(prev_active, reason)

    def _fire_fault_events(
        self, prev_active: bool, prev_current: float, reason: str
    ) -> None:
        """Fire events and create notifications for fault conditions."""
        if reason == REASON_FALLBACK_UNAVAILABLE and self.current_set_a == 0.0:
            self._notify_meter_unavailable()
        elif reason == REASON_FALLBACK_UNAVAILABLE and self.current_set_a > 0:
            self._notify_fallback_activated()
        elif reason == REASON_POWER_METER_UPDATE and prev_active and not self.active:
            self._notify_overload_stop(prev_current)

    def _fire_resolution_events(self, prev_active: bool, reason: str) -> None:
        """Fire events and dismiss notifications when faults resolve."""
        if not prev_active and self.active:
            self._notify_charging_resumed()
        if reason == REASON_POWER_METER_UPDATE:
            self._dismiss_meter_notifications()

    def _notify_meter_unavailable(self) -> None:
        """Fire event and create notification for meter unavailable in stop mode."""
        entry_id = self.entry.entry_id
        self.hass.bus.async_fire(
            EVENT_METER_UNAVAILABLE,
            {"entry_id": entry_id, "power_meter_entity": self._power_meter_entity},
        )
        pn_async_create(
            self.hass,
            (
                f"Power meter `{self._power_meter_entity}` is unavailable. "
                "Charging has been stopped for safety."
            ),
            title="EV Load Balancer — Meter Unavailable",
            notification_id=NOTIFICATION_METER_UNAVAILABLE_FMT.format(entry_id=entry_id),
        )

    def _notify_fallback_activated(self) -> None:
        """Fire event and create notification for fallback current activation."""
        entry_id = self.entry.entry_id
        self.hass.bus.async_fire(
            EVENT_FALLBACK_ACTIVATED,
            {
                "entry_id": entry_id,
                "power_meter_entity": self._power_meter_entity,
                "fallback_current_a": self.current_set_a,
            },
        )
        pn_async_create(
            self.hass,
            (
                f"Power meter `{self._power_meter_entity}` is unavailable. "
                f"Fallback current of {self.current_set_a} A applied."
            ),
            title="EV Load Balancer — Fallback Activated",
            notification_id=NOTIFICATION_FALLBACK_ACTIVATED_FMT.format(entry_id=entry_id),
        )

    def _notify_overload_stop(self, prev_current: float) -> None:
        """Fire event and create notification when overload forces a charging stop."""
        entry_id = self.entry.entry_id
        self.hass.bus.async_fire(
            EVENT_OVERLOAD_STOP,
            {
                "entry_id": entry_id,
                "previous_current_a": prev_current,
                "available_current_a": self.available_current_a,
            },
        )
        pn_async_create(
            self.hass,
            (
                "Household load exceeds the service limit. "
                f"Charging stopped (was {prev_current} A, "
                f"available headroom: {self.available_current_a} A)."
            ),
            title="EV Load Balancer — Overload",
            notification_id=NOTIFICATION_OVERLOAD_STOP_FMT.format(entry_id=entry_id),
        )

    def _notify_charging_resumed(self) -> None:
        """Fire event and dismiss overload notification when charging resumes."""
        entry_id = self.entry.entry_id
        self.hass.bus.async_fire(
            EVENT_CHARGING_RESUMED,
            {"entry_id": entry_id, "current_a": self.current_set_a},
        )
        pn_async_dismiss(
            self.hass,
            NOTIFICATION_OVERLOAD_STOP_FMT.format(entry_id=entry_id),
        )

    def _dismiss_meter_notifications(self) -> None:
        """Dismiss meter/fallback notifications when the meter recovers."""
        entry_id = self.entry.entry_id
        pn_async_dismiss(
            self.hass,
            NOTIFICATION_METER_UNAVAILABLE_FMT.format(entry_id=entry_id),
        )
        pn_async_dismiss(
            self.hass,
            NOTIFICATION_FALLBACK_ACTIVATED_FMT.format(entry_id=entry_id),
        )

    def _has_actions(self) -> bool:
        """Return True if any charger action script is configured."""
        return bool(
            self._action_set_current
            or self._action_stop_charging
            or self._action_start_charging
        )

    async def _execute_actions(
        self, prev_active: bool, prev_current: float
    ) -> None:
        """Execute the appropriate charger action(s) based on state transitions.

        Transition rules:
        - **Resume** (was stopped, now active): call start_charging then set_current.
        - **Stop** (was active, now stopped): call stop_charging.
        - **Adjust** (was active, still active, current changed): call set_current.
        - **No change**: no action is executed.

        Every action receives a ``charger_id`` variable (the config entry ID)
        so scripts can address the correct charger.  The ``set_current`` action
        additionally receives ``current_a`` (amps) and ``current_w`` (watts)
        so charger scripts can use whichever unit their hardware requires.
        """
        new_active = self.active
        new_current = self.current_set_a
        charger_id = self.entry.entry_id
        current_w = round(new_current * self._voltage, 1)

        if new_active and not prev_active:
            # Resume: start charging, then set the target current
            await self._call_action(
                self._action_start_charging,
                "start_charging",
                charger_id=charger_id,
            )
            await self._call_action(
                self._action_set_current,
                "set_current",
                charger_id=charger_id,
                current_a=new_current,
                current_w=current_w,
            )
        elif not new_active and prev_active:
            # Stop charging
            await self._call_action(
                self._action_stop_charging,
                "stop_charging",
                charger_id=charger_id,
            )
        elif new_active and new_current != prev_current:
            # Current changed while active — adjust
            await self._call_action(
                self._action_set_current,
                "set_current",
                charger_id=charger_id,
                current_a=new_current,
                current_w=current_w,
            )

    async def _call_action(
        self,
        entity_id: str | None,
        action_name: str,
        **variables: float | str,
    ) -> None:
        """Call a configured action script with the given variables.

        Silently skips when the action is not configured.  Logs a warning
        and continues when the service call fails so that a single broken
        script does not prevent the remaining actions from executing.
        """
        if not entity_id:
            return

        service_data: dict = {"entity_id": entity_id}
        if variables:
            service_data["variables"] = variables

        try:
            await self.hass.services.async_call(
                "script",
                "turn_on",
                service_data,
                blocking=True,
            )
            _LOGGER.debug(
                "Action %s executed via %s (variables=%s)",
                action_name,
                entity_id,
                variables or {},
            )
        except Exception as exc:  # noqa: BLE001 — catch all so a broken script never crashes the integration
            _LOGGER.warning(
                "Action %s failed via %s: %s",
                action_name,
                entity_id,
                exc,
            )
            entry_id = self.entry.entry_id
            self.hass.bus.async_fire(
                EVENT_ACTION_FAILED,
                {
                    "entry_id": entry_id,
                    "action_name": action_name,
                    "entity_id": entity_id,
                    "error": str(exc),
                },
            )
            pn_async_create(
                self.hass,
                (
                    f"Action script `{entity_id}` failed for action `{action_name}`: "
                    f"{exc}. "
                    "Check your charger action script configuration."
                ),
                title="EV Load Balancer — Action Failed",
                notification_id=NOTIFICATION_ACTION_FAILED_FMT.format(
                    entry_id=entry_id
                ),
            )
