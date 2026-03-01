# Starter Script Templates

Ready-to-use Home Assistant script templates for controlling your EV charger with the Watt-O-Balancer integration. Copy the YAML for your charger type, paste it into a new script in Home Assistant, and adjust the hardware-specific values.

## Available templates

| Charger type | Set current | Stop charging | Start charging |
|---|---|---|---|
| **OCPP** ([lbbrhzn/ocpp](https://github.com/lbbrhzn/ocpp)) | [ocpp-set-current.yaml](ocpp-set-current.yaml) | [ocpp-stop-charging.yaml](ocpp-stop-charging.yaml) | [ocpp-start-charging.yaml](ocpp-start-charging.yaml) |
| **REST API** | [rest-set-current.yaml](rest-set-current.yaml) | [rest-stop-charging.yaml](rest-stop-charging.yaml) | [rest-start-charging.yaml](rest-start-charging.yaml) |
| **Modbus** | [modbus-set-current.yaml](modbus-set-current.yaml) | [modbus-stop-charging.yaml](modbus-stop-charging.yaml) | [modbus-start-charging.yaml](modbus-start-charging.yaml) |
| **Switch/relay** | _(not applicable)_ | [switch-stop-charging.yaml](switch-stop-charging.yaml) | [switch-start-charging.yaml](switch-start-charging.yaml) |

## How to use

1. Choose the template matching your charger type.
2. In Home Assistant, go to **Settings → Automations & Scenes → Scripts → + Add Script**.
3. Switch to **YAML mode** and paste the template content.
4. Adjust hardware-specific values (IP addresses, register addresses, connector IDs, entity IDs).
5. **Test each script independently** from **Developer Tools → Actions** before connecting it to the integration.
6. Configure the scripts in the integration: **Settings → Integrations → Watt-O-Balancer → Configure**.

For detailed guidance, see the [Action Scripts Guide](../documentation/action-scripts-guide.md).
