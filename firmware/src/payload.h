// claude-stick: payload parser interface
//
// Tiny module sitting between ble_server (which hands us raw bytes from
// the GATT RX characteristic) and state (which holds the parsed sample).
// Pure JSON-to-fields logic, no BLE awareness.

#pragma once

#include <cstddef>
#include <cstdint>

namespace payload {

// Parse a JSON payload received over the BLE RX characteristic and
// push it into state. Expected shape:
//
//   {"s":42.3, "sr":1747244520, "w":67.2, "wr":1747813800,
//    "st":"ok", "ts":1747232412}
//
// Required fields: s, sr, w, wr. Optional: st (defaults to "ok"),
// ts (defaults to now()). On any failure (malformed JSON, missing
// required field, out-of-range value), logs to serial, bumps the
// internal error counter, and leaves state untouched.
void parse(const uint8_t* buf, size_t len);

// Cumulative number of parse / validation failures since boot. Useful
// for diagnostic display on screen 3 in a future iteration.
uint32_t getErrorCount();

}  // namespace payload
