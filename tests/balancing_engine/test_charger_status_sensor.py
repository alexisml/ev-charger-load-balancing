"""Tests for the charger status sensor feature.

When a charger status sensor is configured the balancer reads its state to
determine whether the EV is actively drawing current.  If the sensor reports
a state other than 'Charging', the EV draw estimate is zeroed so the
balancer does not over-subtract headroom when the charger is idle.

Covers:
- Available headroom is not over-subtracted when EV is not charging
- Available headroom correctly accounts for EV draw when sensor = Charging
- Behaviour is unchanged when no status sensor is configured
- Status sensor set via the options flow is honoured by the coordinator
"""

from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ev_lb.const import (
    CONF_CHARGER_STATUS_ENTITY,
    CONF_MAX_SERVICE_CURRENT,
    CONF_POWER_METER_ENTITY,
    CONF_VOLTAGE,
    DOMAIN,
)
from conftest import POWER_METER, setup_integration, get_entity_id


class TestChargerStatusSensor:
    """Verify the balancer correctly uses the charger status sensor when configured.

    When a charger status sensor is configured, the balancer reads its state to
    determine whether the EV is actively drawing current.  If the sensor reports
    a state other than 'Charging', the EV draw estimate is zeroed so the
    balancer does not over-subtract headroom when the charger is idle.
    """

    async def test_headroom_not_over_subtracted_when_ev_not_charging(
        self, hass: HomeAssistant
    ) -> None:
        """Available headroom reflects full service capacity when EV is not actively charging.

        If the charger reports it is NOT charging (state != 'Charging'), the
        balancer must not subtract the previously commanded current from the
        available headroom.  This prevents the balancer from under-reporting
        headroom when the EV has finished charging or is paused.
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
            title="EV Load Balancing",
        )
        hass.states.async_set(POWER_METER, "0")
        hass.states.async_set(status_entity, "Available")  # EV not charging
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")

        # 5 kW load at 230 V → 21.7 A draw → headroom = 32 - 21.7 = 10.3 → 10 A
        # EV is not charging, so current_set_a estimate is 0 (not subtracted)
        hass.states.async_set(POWER_METER, "5000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 10.0

    async def test_headroom_accounts_for_ev_draw_when_charging(
        self, hass: HomeAssistant
    ) -> None:
        """Available headroom correctly isolates non-EV load when EV is actively charging.

        When the charger status sensor reports 'Charging', the balancer subtracts
        the last commanded current from the total service draw to isolate the
        non-EV household load before computing the new target.
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
            title="EV Load Balancing",
        )
        hass.states.async_set(POWER_METER, "0")
        hass.states.async_set(status_entity, "Charging")  # EV actively charging
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")

        # First reading at 3 kW: current_set starts at 0, so ev_estimate = 0
        # service = 13.04 A, non-EV = 13.04, available = 18.96 → 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0

        # Second reading at 5 kW: status=Charging, ev_estimate = 18 A
        # service = 21.74 A, non-EV = 21.74 - 18 = 3.74, available = 28.26 → 28 A
        hass.states.async_set(POWER_METER, "5000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 28.0

    async def test_no_status_sensor_behaves_as_before(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Balancing is unaffected when no charger status sensor is configured.

        When the status sensor is absent, the coordinator falls back to the
        original behaviour: the last commanded current is always subtracted from
        the service draw.
        """
        await setup_integration(hass, mock_config_entry)

        current_set_id = get_entity_id(
            hass, mock_config_entry, "sensor", "current_set"
        )

        # 3 kW → current_set = 18 A (no EV draw estimate since current_set was 0)
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0

        # 5 kW: no sensor → assume EV is drawing 18 A → non-EV = 21.74 - 18 = 3.74
        # available = 32 - 3.74 = 28.26 → 28 A
        hass.states.async_set(POWER_METER, "5000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 28.0

    async def test_status_sensor_configured_via_options_flow(
        self, hass: HomeAssistant
    ) -> None:
        """Charger status sensor set via the options flow is honoured by the coordinator.

        Users can configure (or change) the status sensor after initial setup
        via the Configure dialog.  The coordinator must pick up the value from
        options, just like action scripts.
        """
        status_entity = "sensor.ocpp_status"
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_POWER_METER_ENTITY: POWER_METER,
                CONF_VOLTAGE: 230.0,
                CONF_MAX_SERVICE_CURRENT: 32.0,
            },
            options={CONF_CHARGER_STATUS_ENTITY: status_entity},
            title="EV Load Balancing",
        )
        hass.states.async_set(POWER_METER, "0")
        hass.states.async_set(status_entity, "Available")  # EV not charging
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        assert coordinator._charger_status_entity == status_entity
        assert coordinator._is_ev_charging() is False

    async def test_unavailable_sensor_falls_back_to_charging_assumption(
        self, hass: HomeAssistant
    ) -> None:
        """An unavailable or unknown sensor state is treated as 'charging' to stay safe.

        If the OCPP integration goes offline and the sensor becomes 'unavailable'
        or 'unknown', the balancer must not zero out the EV estimate.  Zeroing it
        would over-report available headroom and could send a dangerously high
        current command to the charger.  The safe fallback is to keep assuming
        the EV is drawing its last commanded current.
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
            title="EV Load Balancing",
        )
        hass.states.async_set(POWER_METER, "0")
        hass.states.async_set(status_entity, "Charging")
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

        # Sensor exists but goes unavailable
        hass.states.async_set(status_entity, "unavailable")
        assert coordinator._is_ev_charging() is True

        # Sensor exists but state is unknown
        hass.states.async_set(status_entity, "unknown")
        assert coordinator._is_ev_charging() is True

        # Sensor entity removed from state machine entirely
        hass.states.async_remove(status_entity)
        assert coordinator._is_ev_charging() is True
