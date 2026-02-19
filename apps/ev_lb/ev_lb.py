"""AppDaemon app for EV charger dynamic load balancing.

Listens to a power-meter sensor in Home Assistant and dynamically adjusts the
charging current for one or more OCPP-compatible EV chargers so that the total
household current draw stays within the configured service limit.

Configuration (apps/ev_lb/ev_lb.yaml):
    ev_charger_load_balancer:
      module: ev_lb
      class: EVChargerLoadBalancer
      power_sensor: sensor.house_power_w       # required
      voltage_v: 230                           # optional, default 230
      max_service_current_input: input_number.ev_lb_max_service_current_a
      min_current_input: input_number.ev_lb_min_current_before_shutdown_a
      enabled_input: input_boolean.ev_lb_enabled
      chargers:
        - id: charger_1
          max_charging_current_input: input_number.ev_lb_max_charging_current_a_charger_1
          set_current_service: script.ev_lb_set_current_charger_1
          stop_charging_service: script.ev_lb_stop_charging_charger_1   # optional
          start_charging_service: script.ev_lb_start_charging_charger_1 # optional
"""

from __future__ import annotations

from typing import Optional

try:
    import appdaemon.plugins.hass.hassapi as hass

    _APPDAEMON_AVAILABLE = True
except ImportError:  # pragma: no cover
    _APPDAEMON_AVAILABLE = False

VOLTAGE_DEFAULT: float = 230.0  # Volts
MIN_CURRENT_DEFAULT: float = 6.0  # Amps (IEC 61851 minimum for AC charging)
STEP_DEFAULT: float = 1.0  # Amps — resolution of current adjustments


# ---------------------------------------------------------------------------
# Pure computation functions (no AppDaemon dependency — fully unit-testable)
# ---------------------------------------------------------------------------


def compute_available_current(
    house_power_w: float,
    current_ev_a: float,
    max_service_a: float,
    voltage_v: float = VOLTAGE_DEFAULT,
) -> float:
    """Return the total current available for EV charging.

    The formula derives the non-EV load from the total house power, then
    subtracts it from the service limit:

        non_ev_power_w  = house_power_w  - current_ev_a * voltage_v
        available_ev_a  = max_service_a  - non_ev_power_w / voltage_v

    Args:
        house_power_w:  Current total household power draw in Watts,
                        **including** any active EV charging.
        current_ev_a:   Sum of current charging currents (Amps) across all
                        chargers managed by this app.
        max_service_a:  Whole-house breaker / service rating in Amps.
        voltage_v:      Nominal supply voltage in Volts.

    Returns:
        Available current for EV charging in Amps (may be negative when the
        non-EV load already exceeds the service limit).
    """
    non_ev_power_w = house_power_w - current_ev_a * voltage_v
    non_ev_a = non_ev_power_w / voltage_v
    return max_service_a - non_ev_a


def clamp_current(
    available_a: float,
    max_charger_a: float,
    min_charger_a: float,
    step_a: float = STEP_DEFAULT,
) -> Optional[float]:
    """Clamp *available_a* to charger-specific limits, floored to *step_a*.

    Args:
        available_a:    Current available for this charger in Amps.
        max_charger_a:  Per-charger maximum current limit in Amps.
        min_charger_a:  Per-charger minimum current below which charging
                        should be stopped rather than set to a low value.
        step_a:         Current resolution/step in Amps (default 1 A).

    Returns:
        Target current in Amps, or ``None`` if *available_a* is below the
        charger's minimum (caller should stop charging).
    """
    target = min(available_a, max_charger_a)
    target = (target // step_a) * step_a
    if target < min_charger_a:
        return None
    return target


def distribute_current(
    available_a: float,
    chargers: list[tuple[float, float]],
    step_a: float = STEP_DEFAULT,
) -> list[Optional[float]]:
    """Fairly distribute *available_a* across multiple chargers (water-filling).

    Uses an iterative water-filling algorithm:
    1.  Compute the equal fair share for all active chargers.
    2.  Chargers whose fair share reaches or exceeds their maximum are capped
        at that maximum; the unused headroom is returned to the pool.
    3.  Chargers whose fair share falls below their minimum are shut down
        (allocated ``None``); they do not consume from the pool.
    4.  Repeat until no charger changes state, then assign the final fair
        share to the remaining chargers.

    Args:
        available_a:  Total current available for EV charging in Amps.
        chargers:     List of ``(min_a, max_a)`` tuples, one per charger.
        step_a:       Current resolution in Amps (default 1 A).

    Returns:
        List of target currents (Amps) aligned with *chargers*.  A value of
        ``None`` means the charger should be stopped.
    """
    n = len(chargers)
    if n == 0:
        return []

    allocations: list[Optional[float]] = [None] * n
    active: list[int] = list(range(n))
    remaining: float = available_a

    while active:
        fair_share = remaining / len(active)
        capped: list[int] = []
        below_min: list[int] = []

        for i in active:
            min_a, max_a = chargers[i]
            max_floored = (max_a // step_a) * step_a
            target = (min(fair_share, max_a) // step_a) * step_a

            if target >= max_floored:
                capped.append(i)
            elif target < min_a:
                below_min.append(i)

        if not capped and not below_min:
            # All remaining chargers get the fair share
            for i in active:
                min_a, _ = chargers[i]
                target = (fair_share // step_a) * step_a
                allocations[i] = target if target >= min_a else None
            break

        for i in capped:
            max_floored = (chargers[i][1] // step_a) * step_a
            min_a = chargers[i][0]
            if max_floored >= min_a:
                allocations[i] = max_floored
                remaining -= max_floored
            else:
                allocations[i] = None
            active.remove(i)

        for i in below_min:
            allocations[i] = None
            active.remove(i)

    return allocations


# ---------------------------------------------------------------------------
# AppDaemon app
# ---------------------------------------------------------------------------

if _APPDAEMON_AVAILABLE:  # pragma: no cover

    class EVChargerLoadBalancer(hass.Hass):  # type: ignore[misc]
        """AppDaemon app that performs dynamic EV charger load balancing."""

        def initialize(self) -> None:
            """Called by AppDaemon at startup to register listeners."""
            self._voltage_v: float = float(self.args.get("voltage_v", VOLTAGE_DEFAULT))
            self._power_sensor: str = self.args["power_sensor"]
            self._max_service_input: str = self.args["max_service_current_input"]
            self._min_current_input: str = self.args.get(
                "min_current_input", "input_number.ev_lb_min_current_before_shutdown_a"
            )
            self._enabled_input: str = self.args.get(
                "enabled_input", "input_boolean.ev_lb_enabled"
            )
            self._charger_configs: list[dict] = list(self.args.get("chargers", []))

            # Track the last requested current per charger (index → Amps)
            self._current_set: dict[int, float] = {}

            self.listen_state(self._on_power_change, self._power_sensor)
            self.log("EVChargerLoadBalancer initialised")

        # ------------------------------------------------------------------
        # State-change handler
        # ------------------------------------------------------------------

        def _on_power_change(
            self, entity: str, attribute: str, old: str, new: str, kwargs: dict
        ) -> None:
            if new in ("unavailable", "unknown", None):
                self.log(
                    f"Power sensor {entity} is {new!r}, skipping cycle",
                    level="WARNING",
                )
                return

            if not self._is_enabled():
                return

            try:
                house_power_w = float(new)
            except ValueError:
                self.log(f"Cannot parse power sensor value {new!r}", level="WARNING")
                return

            self._run_balancing(house_power_w)

        # ------------------------------------------------------------------
        # Balancing logic
        # ------------------------------------------------------------------

        def _run_balancing(self, house_power_w: float) -> None:
            max_service_a = self._get_float_state(self._max_service_input)
            min_current_a = self._get_float_state(
                self._min_current_input, default=MIN_CURRENT_DEFAULT
            )

            charger_specs: list[tuple[float, float]] = []
            for idx, cfg in enumerate(self._charger_configs):
                max_charger_a = self._get_float_state(
                    cfg["max_charging_current_input"]
                )
                charger_specs.append((min_current_a, max_charger_a))

            current_ev_a = sum(self._current_set.get(i, 0.0) for i in range(len(self._charger_configs)))

            available_a = compute_available_current(
                house_power_w=house_power_w,
                current_ev_a=current_ev_a,
                max_service_a=max_service_a,
                voltage_v=self._voltage_v,
            )

            targets = distribute_current(
                available_a=available_a,
                chargers=charger_specs,
            )

            for idx, (cfg, target) in enumerate(zip(self._charger_configs, targets)):
                charger_id = cfg["id"]
                prev = self._current_set.get(idx)

                if target is None:
                    if prev is not None and prev > 0:
                        self._stop_charging(cfg, charger_id)
                    self._current_set[idx] = 0.0
                    self._set_active_sensor(charger_id, False)
                else:
                    was_stopped = prev is None or prev == 0.0
                    if was_stopped:
                        self._start_charging(cfg, charger_id)
                    if target != prev:
                        self._set_current(cfg, charger_id, target)
                    self._current_set[idx] = target
                    self._set_active_sensor(charger_id, True)

                self._set_current_sensor(charger_id, self._current_set[idx])

        # ------------------------------------------------------------------
        # Service helpers
        # ------------------------------------------------------------------

        def _set_current(self, cfg: dict, charger_id: str, current_a: float) -> None:
            service = cfg.get("set_current_service", "")
            if not service:
                return
            domain, name = service.split(".", 1)
            self.call_service(
                f"{domain}/{name}",
                charger_id=charger_id,
                current_a=current_a,
            )
            self.log(f"[{charger_id}] set_current → {current_a} A")

        def _stop_charging(self, cfg: dict, charger_id: str) -> None:
            service = cfg.get("stop_charging_service", "")
            if not service:
                return
            domain, name = service.split(".", 1)
            self.call_service(f"{domain}/{name}", charger_id=charger_id)
            self.log(f"[{charger_id}] stop_charging called")

        def _start_charging(self, cfg: dict, charger_id: str) -> None:
            service = cfg.get("start_charging_service", "")
            if not service:
                return
            domain, name = service.split(".", 1)
            self.call_service(f"{domain}/{name}", charger_id=charger_id)
            self.log(f"[{charger_id}] start_charging called")

        # ------------------------------------------------------------------
        # Entity state helpers
        # ------------------------------------------------------------------

        def _set_active_sensor(self, charger_id: str, active: bool) -> None:
            entity_id = f"binary_sensor.ev_lb_{charger_id}_active"
            self.set_state(entity_id, state="on" if active else "off")

        def _set_current_sensor(self, charger_id: str, current_a: float) -> None:
            entity_id = f"sensor.ev_lb_{charger_id}_current_set"
            self.set_state(
                entity_id,
                state=current_a,
                attributes={"unit_of_measurement": "A", "device_class": "current"},
            )

        def _is_enabled(self) -> bool:
            state = self.get_state(self._enabled_input)
            return str(state).lower() not in ("off", "false", "0", "no")

        def _get_float_state(self, entity_id: str, default: float = 0.0) -> float:
            raw = self.get_state(entity_id)
            if raw in ("unavailable", "unknown", None):
                return default
            try:
                return float(raw)
            except (TypeError, ValueError):
                return default
