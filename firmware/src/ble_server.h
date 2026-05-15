// claude-stick: BLE GATT server interface
//
// One custom service with two characteristics:
//
//   RX (write / write-no-response): JSON payloads from the daemon
//   TX (notify):                    we fire 0x01 here on subscribe to
//                                   ask the daemon to send a fresh
//                                   sample immediately, rather than
//                                   waiting up to 60 seconds for its
//                                   next poll cycle
//
// Also handles connection-state callbacks, pushing BleState changes
// into the state module so screen 3 can show them.

#pragma once

namespace ble_server {

// Bring up NimBLE, register the service and callbacks, start
// advertising. Call from setup() after state::init() (we need the
// MAC and device name already stamped into state).
void init();

// Send a 0x01 refresh request notification on the TX characteristic.
// Called automatically on subscribe; exposed here so a future button
// long-press could trigger a manual refresh. No-op if no peer is
// subscribed to TX.
void requestRefresh();

// Drive the BLE watchdog and heartbeat log. Call from loop() at any
// reasonable rate (rate-limits internally). The watchdog ensures
// advertising restarts if it ever stops while no peer is connected.
void tick();

}  // namespace ble_server
