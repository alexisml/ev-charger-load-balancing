"""Pure computation functions for EV charger dynamic load balancing.

This module contains the core balancing logic used by the integration
runtime.  It has no dependency on Home Assistant — it can be tested
with plain pytest.

Functions:
    compute_available_current   — headroom above current total draw
    clamp_current               — per-charger min/max/step clamping
    distribute_current          — water-filling distribution across N chargers
    apply_ramp_up_limit         — cooldown before allowing current increase
"""

from __future__ import annotations

from typing import Optional

VOLTAGE_DEFAULT: float = 230.0  # Volts
MIN_CURRENT_DEFAULT: float = 6.0  # Amps (IEC 61851 minimum for AC charging)
STEP_DEFAULT: float = 1.0  # Amps — resolution of current adjustments
RAMP_UP_TIME_DEFAULT: float = 30.0  # Seconds — cooldown before increasing current


def compute_available_current(
    house_power_w: float,
    max_service_a: float,
    voltage_v: float = VOLTAGE_DEFAULT,
) -> float:
    """Return the current headroom available above the current total draw.

    The formula converts the total metered house power (including any active
    EV charging) into Amps and subtracts it from the service limit:

        available_a = max_service_a - house_power_w / voltage_v

    A positive value means there is headroom to increase EV charging; a
    negative value means the service limit is already exceeded and the EV
    current must be reduced immediately.

    Args:
        house_power_w:  Current total household power draw in Watts,
                        **including** any active EV charging.
        max_service_a:  Whole-house breaker / service rating in Amps.
        voltage_v:      Nominal supply voltage in Volts.

    Returns:
        Headroom in Amps above the current total draw.  May be negative when
        total consumption already exceeds the service limit.
    """
    return max_service_a - house_power_w / voltage_v


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
