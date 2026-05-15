// claude-stick: application state interface
//
// This header declares the data contract that other modules see. The
// actual storage, mutex and update logic live in state.cpp. display.cpp
// reads only via state::getSnapshot(), which copies the internal state
// out under lock and returns a self-contained struct that's safe to
// walk without further synchronisation.

#pragma once

#include <cstdint>
#include <ctime>

namespace state {

// ─── Enums ─────────────────────────────────────────────────────────────

enum class Screen : uint8_t {
  Main = 0,
  Detail = 1,
  Connection = 2,
  Count = 3,
};

enum class Status : uint8_t {
  Ok,             // payload received recently, daemon healthy
  Stale,          // last payload older than cfg::SYNC_STALE_SEC
  RateLimited,    // daemon hit Anthropic's rate limits (st="rate")
  AuthFailed,     // OAuth token problem on daemon side (st="auth")
  NetFailed,      // daemon couldn't reach Anthropic at all (st="net")
};

enum class TrendDir : int8_t {
  Down = -1,
  Flat = 0,
  Up = 1,
};

enum class BleState : uint8_t {
  Off,
  Advertising,    // visible, no peer paired
  Paired,         // bonded but daemon not currently connected
  Connected,      // daemon actively connected
};

enum class WifiState : uint8_t {
  Off,
  Connecting,
  Online,
  Failed,
};

// ─── Snapshot ──────────────────────────────────────────────────────────
// A point-in-time copy of everything the display needs to render any of
// the three screens. Returned by getSnapshot() and consumed read-only.

struct Snapshot {
  Screen screen;

  // Utilisation samples. -1.0f means "no data yet" and display renders
  // these as "--%" rather than "0%".
  float sessionPct;
  float weeklyPct;

  // Trend symbols and the underlying signed pp deltas (used on screen 2).
  TrendDir sessionTrend;
  TrendDir weeklyTrend;
  float    sessionDelta;
  float    weeklyDelta;

  // All times are epoch seconds. `now` is captured at snapshot time
  // so durations are computed against a single consistent reference.
  time_t sessionResetAt;
  time_t weeklyResetAt;
  time_t lastSyncAt;       // 0 means "never synced"
  time_t now;

  // Effective status. Computed at snapshot time: the daemon-reported
  // status is auto-promoted to Stale once the sync age exceeds
  // cfg::SYNC_STALE_SEC, so display only ever needs to glance at one
  // field to colour the footer dot.
  Status status;

  // Connection screen fields.
  BleState  ble;
  WifiState wifi;
  int8_t    bleRssi;       // dBm; INT8_MIN means unknown
  int8_t    wifiRssi;
  char      wifiSsid[32];
  char      deviceName[24];
  char      macStr[18];    // "AA:BB:CC:DD:EE:FF"
  time_t    ntpLastSyncAt; // 0 means "never synced"

  // Battery (read fresh from the AXP192 on every snapshot).
  float   batteryVolts;
  uint8_t batteryPct;
  bool    batteryCharging;

  // System.
  uint32_t uptimeSec;
  char     firmware[12];

  // Monotonic version counter, bumped on every mutation. The display
  // could compare against its last-seen value to skip redraws; currently
  // it ticks at a fixed rate to keep countdowns moving anyway.
  uint32_t version;
};

// ─── Public API ────────────────────────────────────────────────────────

// Initialise internal storage. Creates the mutex, reads the BLE MAC,
// copies static strings (device name, firmware) from cfg into state.
// Must be called once in setup() after M5.begin() and before any
// other state:: function.
void init();

// Returns a fully-populated snapshot of the current state. Briefly
// acquires the internal mutex. Cheap; safe to call once per display
// tick. Reads battery and uptime fresh from M5.Power / millis().
Snapshot getSnapshot();

// Advance to the next screen (PWR or any test button).
void cycleScreen();

// Push a fresh utilisation sample. Computes trend deltas versus the
// previous sample (or marks them flat on the first one). Called by the
// BLE payload parser when a valid JSON blob arrives.
void setSample(float sessionPct, time_t sessionResetAt,
               float weeklyPct,  time_t weeklyResetAt,
               Status status,    time_t daemonTs);

// BLE / WiFi state updates from the relevant modules. setBleRssi /
// setWifiInfo don't bump the version on RSSI alone, since signal
// strength fluctuates faster than the display usefully tracks.
void setBleState(BleState s);
void setBleRssi(int8_t rssi);
void setWifiState(WifiState s);
void setWifiInfo(const char* ssid, int8_t rssi);

// NTP sync completion. Stamps `now` as the last successful sync.
void setNtpSynced();

}  // namespace state
