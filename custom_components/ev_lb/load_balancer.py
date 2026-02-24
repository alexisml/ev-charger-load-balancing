"""Pure computation functions for EV charger dynamic load balancing.

This module contains the core balancing logic used by the integration
runtime.  It has no dependency on Home Assistant — it can be tested
with plain pytest.

Functions:
    compute_available_current   — max EV current given a non-EV power draw
    compute_target_current      — full single-charger target from whole-house meter
    clamp_current               — per-charger min/max/step clamping
    distribute_current          — water-filling distribution across N chargers
    apply_ramp_up_limit         — cooldown before allowing current increase
"""

from __future__ import annotations

from typing import Optional

VOLTAGE_DEFAULT: float = 230.0  # Volts
STEP_DEFAULT: float = 1.0  # Amps — resolution of current adjustments


def compute_available_current(
    house_power_w: float,
    max_service_a: float,
    voltage_v: float = VOLTAGE_DEFAULT,
) -> float:
    """Return the current available for EV charging given the supplied power draw.

    The formula converts the metered power into Amps and subtracts it from
    the service limit:

        available_a = max_service_a - house_power_w / voltage_v

    The coordinator calls this function with the **non-EV** household load
    (after subtracting the EV's estimated contribution from the whole-house
    reading), so the returned value is the maximum current the EV can safely
    draw without exceeding the service limit.

    A positive value means the EV can charge at that current.  A negative
    value means the non-EV load alone already exceeds the service limit and
    the EV must be stopped.

    Args:
        house_power_w:  Power draw to account for in Watts.  The coordinator
                        passes the **non-EV** portion of the whole-house meter.
        max_service_a:  Whole-house breaker / service rating in Amps.
        voltage_v:      Nominal supply voltage in Volts.

    Returns:
        Maximum current available for EV charging in Amps.  May be negative
        when the non-EV load alone exceeds the service limit.
    """
    return max_service_a - house_power_w / voltage_v


def compute_target_current(
    house_power_w: float,
    current_set_a: float,
    max_service_a: float,
    max_charger_a: float,
    min_charger_a: float,
    voltage_v: float = VOLTAGE_DEFAULT,
    step_a: float = STEP_DEFAULT,
) -> tuple[float, Optional[float]]:
    """Compute the target charging current and available current from a whole-house meter.

    This is the single-charger balancing formula.  It isolates the non-EV
    household load by subtracting the EV's estimated draw from the whole-house
    meter reading, then derives the maximum current the EV can safely draw.

    When *current_set_a* is 0 (EV idle or stopped), the formula reduces to
    the simple ``max_service_a − house_power_w / voltage_v``.

    Args:
        house_power_w:   Whole-house power reading in Watts, including any
                         active EV charging.
        current_set_a:   The current the integration last commanded to the
                         charger in Amps (used to estimate the EV's draw).
        max_service_a:   Whole-house breaker / service rating in Amps.
        max_charger_a:   Per-charger maximum current limit in Amps.
        min_charger_a:   Per-charger minimum current below which charging
                         should be stopped rather than set to a low value.
        voltage_v:       Nominal supply voltage in Volts.
        step_a:          Current resolution in Amps (default 1 A).

    Returns:
        A ``(available_a, target_a)`` tuple where *available_a* is the maximum
        current the EV can safely draw (before charger-limit clamping) and
        *target_a* is the clamped target in Amps, or ``None`` if the available
        current is below the charger's minimum (caller should stop charging).
    """
    ev_power_w = current_set_a * voltage_v
    non_ev_power_w = max(0.0, house_power_w - ev_power_w)
    available_a = compute_available_current(non_ev_power_w, max_service_a, voltage_v)
    target_a = clamp_current(available_a, max_charger_a, min_charger_a, step_a)
    return available_a, target_a


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


def _classify_chargers(
    active: list[int],
    chargers: list[tuple[float, float]],
    fair_share: float,
    step_a: float,
) -> tuple[list[int], list[int]]:
    """Split active charger indices into capped and below-minimum groups.

    Args:
        active:     Indices of chargers still competing for current.
        chargers:   ``(min_a, max_a)`` tuples for every charger.
        fair_share: Equal share of remaining current per active charger.
        step_a:     Current resolution in Amps.

    Returns:
        ``(capped, below_min)`` — indices of chargers that hit their
        maximum or fell below their minimum, respectively.
    """
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

    return capped, below_min


def _assign_final_shares(
    active: list[int],
    chargers: list[tuple[float, float]],
    fair_share: float,
    step_a: float,
    allocations: list[Optional[float]],
) -> None:
    """Assign the final fair share to each remaining active charger in-place.

    Called when no charger needs capping or removal — the iteration is done.
    """
    for i in active:
        min_a, _ = chargers[i]
        target = (fair_share // step_a) * step_a
        allocations[i] = target if target >= min_a else None


def _settle_capped_and_below_min(
    capped: list[int],
    below_min: list[int],
    chargers: list[tuple[float, float]],
    step_a: float,
    active: list[int],
    allocations: list[Optional[float]],
    remaining: float,
) -> float:
    """Allocate capped chargers at their max and remove below-minimum chargers.

    Returns the updated remaining current after subtracting capped allocations.
    """
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

    return remaining


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
        capped, below_min = _classify_chargers(
            active, chargers, fair_share, step_a
        )

        if not capped and not below_min:
            _assign_final_shares(
                active, chargers, fair_share, step_a, allocations
            )
            break

        remaining = _settle_capped_and_below_min(
            capped, below_min, chargers, step_a, active, allocations, remaining
        )

    return allocations


def apply_ramp_up_limit(
    prev_a: float,
    target_a: float,
    last_reduction_time: Optional[float],
    now: float,
    ramp_up_time_s: float,
) -> float:
    """Prevent increasing current before the ramp-up cooldown has elapsed.

    **Reductions are always applied instantly** — this function never delays a
    decrease in current.  Only increases are subject to the cooldown: after a
    dynamic current reduction the app waits *ramp_up_time_s* seconds before
    allowing the target to rise again.  This avoids oscillation when household
    load fluctuates around the service limit.

    Args:
        prev_a:              Current charging current in Amps (last set value).
        target_a:            Newly computed target current in Amps.
        last_reduction_time: Monotonic timestamp (seconds) when the current was
                             last reduced for this charger, or ``None`` if there
                             has been no reduction yet.
        now:                 Current monotonic timestamp in seconds.
        ramp_up_time_s:      Cooldown period in seconds before an increase is
                             allowed after a reduction.

    Returns:
        *target_a* immediately when the target is lower than or equal to
        *prev_a* (instant reduction), or when no prior reduction has been
        recorded, or when the cooldown has already elapsed.  Returns *prev_a*
        (hold) only when the cooldown period has not yet elapsed.
    """
    if target_a > prev_a and last_reduction_time is not None:
        elapsed = now - last_reduction_time
        if elapsed < ramp_up_time_s:
            return prev_a
    return target_a
