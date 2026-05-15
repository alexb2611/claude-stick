// claude-stick: WiFi + NTP time sync interface.
//
// Seeds the system clock from the BM8563 battery-backed RTC at boot,
// configures the timezone (POSIX TZ string in cfg::TIMEZONE_TZ), then
// brings up WiFi and runs SNTP. Once NTP succeeds, the result is
// written back to the RTC so subsequent power cycles come up with a
// good clock immediately, before WiFi has had time to reconnect.
//
// Drives the wifi/ntp fields in state so screen 3 can show real
// connection status and signal strength.

#pragma once

namespace wifi_time {

// Read the RTC, set timezone, start WiFi connection. Call from setup()
// after state::init() and before ble_server::init(). Non-blocking; the
// actual WiFi connect and NTP sync happen asynchronously and are
// progressed by tick().
void init();

// Drive the connect / sync state machine. Call from loop(); rate-limits
// itself to about 1 Hz so it's cheap to spam.
void tick();

}  // namespace wifi_time
