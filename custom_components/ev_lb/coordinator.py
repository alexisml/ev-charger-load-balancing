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
from collections.abc import Callable
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_state_change_event, async_track_time_interval

from homeassistant.components.persistent_notification import (
    async_create as pn_async_create,
    async_dismiss as pn_async_dismiss,
)

from .const import (
    CONF_ACTION_SET_CURRENT,
    CONF_ACTION_START_CHARGING,
    CONF_ACTION_STOP_CHARGING,
    CONF_CHARGER_STATUS_ENTITY,
    CONF_MAX_SERVICE_CURRENT,
    CONF_POWER_METER_ENTITY,
    CONF_UNAVAILABLE_BEHAVIOR,
    CONF_UNAVAILABLE_FALLBACK_CURRENT,
    CONF_VOLTAGE,
    CHARGING_STATE_VALUE,
    DEFAULT_MAX_CHARGER_CURRENT,
    DEFAULT_MIN_EV_CURRENT,
    DEFAULT_OVERLOAD_LOOP_INTERVAL,
    DEFAULT_OVERLOAD_TRIGGER_DELAY,
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
    STATE_DISABLED,
    STATE_STOPPED,
)
from .load_balancer import (
    apply_ramp_up_limit,
    clamp_current,
    clamp_to_safe_output,
    compute_fallback_reapply,
    compute_target_current,
    resolve_balancer_state,
    resolve_fallback_current,
)
from ._log import get_logger

_LOGGER = get_logger(__name__)


class EvLoadBalancerCoordinator:
    """Coordinate power-meter events and EV charger balancing logic.

    Listens for power-meter state changes, computes the target charging
    current, applies the ramp-up cooldown, and publishes the result via
    the HA dispatcher so entity platforms can update.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the coordinator from config entry data."""
        self.hass = hass
        self.entry = entry

        # Config entry values — options take priority over data so changes
        # made via the Configure dialog take effect after the entry reloads.
        _cfg = {**entry.data, **entry.options}
        self._voltage: float = _cfg[CONF_VOLTAGE]
        self._max_service_current: float = _cfg[CONF_MAX_SERVICE_CURRENT]
        self._power_meter_entity: str = entry.data[CONF_POWER_METER_ENTITY]
        self._unavailable_behavior: str = _cfg.get(
            CONF_UNAVAILABLE_BEHAVIOR,
            DEFAULT_UNAVAILABLE_BEHAVIOR,
        )
        self._unavailable_fallback_a: float = _cfg.get(
            CONF_UNAVAILABLE_FALLBACK_CURRENT,
            DEFAULT_UNAVAILABLE_FALLBACK_CURRENT,
        )

        self._init_action_scripts(entry)

        # Runtime parameters (updated by number/switch entities)
        self.max_charger_current: float = DEFAULT_MAX_CHARGER_CURRENT
        self.min_ev_current: float = DEFAULT_MIN_EV_CURRENT
        self.enabled: bool = True
        self.ramp_up_time_s: float = DEFAULT_RAMP_UP_TIME
        self.overload_trigger_delay_s: float = DEFAULT_OVERLOAD_TRIGGER_DELAY
        self.overload_loop_interval_s: float = DEFAULT_OVERLOAD_LOOP_INTERVAL

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

        # Overload correction loop tracking
        self._overload_trigger_unsub: Callable[[], None] | None = None
        self._overload_loop_unsub: Callable[[], None] | None = None

        # Dispatcher signal name
        self.signal_update: str = SIGNAL_UPDATE_FMT.format(
            entry_id=entry.entry_id,
        )

        # Listener removal callback
        self._unsub_listener: Callable[[], None] | None = None

    @property
    def current_set_w(self) -> float:
        """Return the last requested charging power in Watts."""
        return round(self.current_set_a * self._voltage, 1)

    def _init_action_scripts(self, entry: ConfigEntry) -> None:
        """Load action script entity IDs and charger status sensor from the config entry.

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
        self._charger_status_entity: str | None = entry.options.get(
            CONF_CHARGER_STATUS_ENTITY,
            entry.data.get(CONF_CHARGER_STATUS_ENTITY),
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

        if self.hass.is_running:
            # Integration was loaded after HA finished starting (e.g., added
            # via the UI or reloaded after an options change).  Entity platforms
            # are already fully set up and dispatcher connections are live.
            meter_state = self.hass.states.get(self._power_meter_entity)
            if meter_state is None or meter_state.state in ("unavailable", "unknown"):
                self.meter_healthy = False
                self.fallback_active = True
                self._apply_fallback_current()
            else:
                # Meter is healthy.  Schedule a deferred initial recompute so
                # that entity async_added_to_hass handlers (which restore
                # current_set_a from storage) execute before we compute.  The
                # guard inside the task prevents unnecessary computation on a
                # fresh install where current_set_a is still 0.
                self.hass.async_create_task(
                    self._async_initial_recompute(),
                    eager_start=False,
                )
        else:
            # HA is still loading — dependent integrations may not have
            # registered their entities yet, so a missing or unavailable meter
            # state is likely transient.  Defer the health check until HA
            # reports it is fully started to avoid spurious warnings and
            # premature charger actions.
            self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED,
                self._handle_ha_started,
            )

    @callback
    def async_stop(self) -> None:
        """Stop listening and clean up."""
        if self._unsub_listener is not None:
            self._unsub_listener()
            self._unsub_listener = None
        self._cancel_overload_timers()
        _LOGGER.debug("Coordinator stopped")

    @callback
    def _handle_ha_started(self, _event: Event) -> None:
        """Evaluate meter health once HA has fully started.

        Called exactly once via ``EVENT_HOMEASSISTANT_STARTED``, at which
        point every integration has had a chance to register its entities.
        A missing or unavailable power-meter state at this point is a genuine
        problem rather than a transient startup artefact.

        Guards against the entry being unloaded before HA finishes starting
        by checking whether the state-change listener is still active.
        """
        if self._unsub_listener is None:
            # Coordinator was stopped before HA finished starting — nothing to do
            return

        meter_state = self.hass.states.get(self._power_meter_entity)
        if meter_state is None or meter_state.state in ("unavailable", "unknown"):
            self.meter_healthy = False
            self.fallback_active = True
            self._apply_fallback_current()
        else:
            # Meter is healthy.  By the time EVENT_HOMEASSISTANT_STARTED fires
            # all entity async_added_to_hass handlers have already run, so
            # current_set_a is fully restored from storage.  Trigger an initial
            # recompute so the charger is set correctly even when the meter has
            # not changed value since the last HA restart (which would produce
            # no STATE_CHANGED event).
            self._force_recompute_from_meter()
            self._update_overload_timers()
        _LOGGER.debug(
            "HA started — power meter %s is %s",
            self._power_meter_entity,
            "unavailable" if not self.meter_healthy else "healthy",
        )

    async def _async_initial_recompute(self) -> None:
        """Perform the first meter read after the integration loads at runtime.

        Scheduled as a task from :meth:`async_start` so that entity
        ``async_added_to_hass`` handlers — which restore ``current_set_a``
        from storage — execute before the balancing algorithm runs.

        Only triggers a real computation when there is a previous commanded
        current to work from (``current_set_a > 0``).  Skipping the compute
        on a fresh install (where ``current_set_a`` is still 0.0) avoids
        spuriously commanding the charger to max current before the first
        genuine power-meter event arrives.
        """
        if self.current_set_a > 0:
            self._force_recompute_from_meter()
            self._update_overload_timers()

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
            self._cancel_overload_timers()
            self._apply_fallback_current()
            return

        try:
            service_power_w = float(new_state.state)
        except (ValueError, TypeError):
            _LOGGER.warning(
                "Could not parse power meter value: %s", new_state.state
            )
            return

        if abs(service_power_w) > SAFETY_MAX_POWER_METER_W:
            _LOGGER.warning(
                "Power meter value %.0f W exceeds safety limit (%.0f W) "
                "— ignoring as likely sensor error",
                service_power_w,
                SAFETY_MAX_POWER_METER_W,
            )
            return

        self._recompute(service_power_w)
        self._update_overload_timers()

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
                "Parameter changed but power meter is %s "
                "— reapplying fallback with updated parameter limits",
                state.state if state else "missing",
            )
            self.meter_healthy = False
            self.fallback_active = True
            self._reapply_fallback_limits()
            return

        try:
            service_power_w = float(state.state)
        except (ValueError, TypeError):
            return

        if abs(service_power_w) > SAFETY_MAX_POWER_METER_W:
            _LOGGER.warning(
                "Power meter value %.0f W exceeds safety limit (%.0f W) "
                "— ignoring as likely sensor error",
                service_power_w,
                SAFETY_MAX_POWER_METER_W,
            )
            return

        _LOGGER.debug(
            "Runtime parameter changed — recomputing with last meter value %.1f W",
            service_power_w,
        )
        self._recompute(service_power_w, REASON_PARAMETER_CHANGE)

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

    def _reapply_fallback_limits(self) -> None:
        """Reapply the fallback current enforcing updated charger parameter limits.

        Called when a runtime parameter (e.g. max charger current or min EV
        current) changes while the power meter is already unavailable.  Unlike
        :meth:`_apply_fallback_current`, this method does **not** re-fire fault
        events or persistent notifications — those were already issued when the
        meter first became unavailable.

        Covers all three fallback modes:

        - **stop**: applies 0 A (idempotent; action scripts only fire when
          transitioning from active to stopped).
        - **set_current**: recomputes ``min(fallback, new_max_charger)`` and
          updates the charger if the capped value differs.
        - **ignore**: re-clamps ``current_set_a`` to the new charger limits
          and updates if the value has changed.
        """
        target = compute_fallback_reapply(
            self._unavailable_behavior,
            self._unavailable_fallback_a,
            self.max_charger_current,
            self.current_set_a,
            self.min_ev_current,
        )

        if target != self.current_set_a:
            _LOGGER.debug(
                "Fallback current updated after parameter change: %.1f A → %.1f A",
                self.current_set_a,
                target,
            )
            self._update_and_notify(self.available_current_a, target, REASON_PARAMETER_CHANGE)
        else:
            async_dispatcher_send(self.hass, self.signal_update)

    def _apply_fallback_current(self) -> None:
        """Handle the power meter becoming unavailable or unknown.

        Resolves the appropriate fallback and applies it.  The "ignore"
        mode keeps the last computed value; all other modes update the
        charger current via ``_update_and_notify``.

        Callers are responsible for setting ``meter_healthy = False`` and
        ``fallback_active = True`` before invoking this method.
        """
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
        result = resolve_fallback_current(
            self._unavailable_behavior,
            self._unavailable_fallback_a,
            self.max_charger_current,
        )
        if result is None:
            _LOGGER.debug(
                "Power meter %s is unavailable — ignoring (keeping last value %.1f A)",
                self._power_meter_entity,
                self.current_set_a,
            )
        elif result == 0.0:
            _LOGGER.warning(
                "Power meter %s is unavailable — stopping charging (0 A)",
                self._power_meter_entity,
            )
        else:
            _LOGGER.warning(
                "Power meter %s is unavailable — applying fallback current %.1f A "
                "(configured %.1f A, capped to max charger current %.1f A)",
                self._power_meter_entity,
                result,
                self._unavailable_fallback_a,
                self.max_charger_current,
            )
        return result

    # ------------------------------------------------------------------
    # Core computation
    # ------------------------------------------------------------------

    def _is_ev_charging(self) -> bool:
        """Return True if the charger status sensor indicates the EV is actively charging.

        When no status sensor is configured, the coordinator assumes the EV is
        drawing current equal to the last commanded value.  When a sensor is
        configured, the EV draw estimate is zeroed out if the sensor's state is
        not 'Charging' — this prevents the balancer from over-subtracting headroom
        when the charger is idle, paused, or finished.
        """
        if self._charger_status_entity is None:
            return True  # No sensor configured; assume charging when current > 0
        state = self.hass.states.get(self._charger_status_entity)
        if state is None or state.state in ("unavailable", "unknown"):
            return True  # Sensor unavailable; safe fallback — assume charging
        return state.state == CHARGING_STATE_VALUE

    # ------------------------------------------------------------------
    # Overload correction loop
    # ------------------------------------------------------------------

    def _update_overload_timers(self) -> None:
        """Start or cancel the overload correction loop based on the latest available current.

        Called after every normal recompute triggered by a power-meter event.
        When the system is overloaded (available current < 0) and no loop is
        already running, schedules a trigger after *overload_trigger_delay_s*
        seconds.  When the system is no longer overloaded, all pending timers
        are cancelled immediately.
        """
        if self.available_current_a < 0:
            if self._overload_trigger_unsub is None and self._overload_loop_unsub is None:
                self._overload_trigger_unsub = async_call_later(
                    self.hass,
                    self.overload_trigger_delay_s,
                    self._on_overload_triggered,
                )
                _LOGGER.debug(
                    "Overload detected (%.1f A) — correction loop starts in %.0f s",
                    self.available_current_a,
                    self.overload_trigger_delay_s,
                )
        else:
            self._cancel_overload_timers()

    def _cancel_overload_timers(self) -> None:
        """Cancel any pending overload trigger delay and running correction loop."""
        if self._overload_trigger_unsub is not None:
            self._overload_trigger_unsub()
            self._overload_trigger_unsub = None
        if self._overload_loop_unsub is not None:
            self._overload_loop_unsub()
            self._overload_loop_unsub = None

    @callback
    def _on_overload_triggered(self, _now) -> None:
        """Fire the first correction after the trigger delay has elapsed.

        If the system is still overloaded, applies an immediate correction and
        starts the periodic loop.  If the overload has already cleared, does
        nothing.
        """
        self._overload_trigger_unsub = None
        self._force_recompute_from_meter()
        if self.available_current_a < 0 and self._overload_loop_unsub is None:
            self._overload_loop_unsub = async_track_time_interval(
                self.hass,
                self._overload_loop_callback,
                timedelta(seconds=self.overload_loop_interval_s),
            )
            _LOGGER.debug(
                "Overload persists — correction loop running every %.0f s",
                self.overload_loop_interval_s,
            )

    @callback
    def _overload_loop_callback(self, _now) -> None:
        """Re-run the balancing algorithm during the overload correction loop.

        Called periodically while the system is overloaded.  Cancels the loop
        once the available current returns to zero or above.
        """
        self._force_recompute_from_meter()
        if self.available_current_a >= 0:
            _LOGGER.debug("Overload cleared — stopping correction loop")
            self._cancel_overload_timers()

    def _force_recompute_from_meter(self) -> None:
        """Read the current power-meter state and recompute without waiting for a state change."""
        if not self.enabled:
            return
        state = self.hass.states.get(self._power_meter_entity)
        if state is None or state.state in ("unavailable", "unknown"):
            return
        try:
            service_power_w = float(state.state)
        except (ValueError, TypeError):
            return
        if abs(service_power_w) > SAFETY_MAX_POWER_METER_W:
            return
        self._recompute(service_power_w)

    def _recompute(self, service_power_w: float, reason: str = REASON_POWER_METER_UPDATE) -> None:
        """Run the balancing algorithm for this instance and publish updates."""
        service_current_a = service_power_w / self._voltage
        # When we know the EV is not actively charging, do not subtract its
        # last commanded current from the available headroom estimate.
        ev_current_estimate = self.current_set_a if self._is_ev_charging() else 0.0
        available_a, clamped = compute_target_current(
            service_current_a,
            ev_current_estimate,
            self._max_service_current,
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
            "Recompute (%s): service=%.0f W, available=%.1f A, clamped=%.1f A, final=%.1f A",
            reason,
            service_power_w,
            available_a,
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
        ramp_up_held = final_a < target_a

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
        clamped_a = clamp_to_safe_output(current_a, self.max_charger_current, self._max_service_current)
        if clamped_a != current_a:
            _LOGGER.warning(
                "Safety clamp: computed %.1f A exceeds safe maximum %.1f A "
                "(charger_max=%.1f, service_max=%.1f) — clamping",
                current_a,
                clamped_a,
                self.max_charger_current,
                self._max_service_current,
            )
            current_a = clamped_a

        prev_active = self.active
        prev_current = self.current_set_a

        self.available_current_a = available_a
        self.current_set_a = current_a
        self.active = current_a > 0
        self.last_action_reason = reason

        # Determine balancer operational state
        self.balancer_state = resolve_balancer_state(
            self.enabled, self.active, prev_active, prev_current, self.current_set_a, ramp_up_held,
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

        Silently skips when the action is not configured.  When the script
        call fails for any reason, logs a warning, fires an
        ``ev_lb_action_failed`` event, creates a persistent dashboard
        notification, and continues so that a single broken script does not
        prevent the remaining actions from executing.
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
