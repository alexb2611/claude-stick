// claude-stick: application state implementation
//
// Holds the live AppState struct, protects it with a FreeRTOS mutex,
// and exposes setters for each input module (payload parser, BLE
// server, WiFi/NTP) to push updates into.
//
// getSnapshot() copies the state out under the lock and computes a few
// derived fields: the effective status (auto-promoted to Stale once
// the sync age exceeds cfg::SYNC_STALE_SEC), and fresh battery and
// uptime readings from M5.Power and millis(). Hardware reads happen
// before the lock so we hold it for as little time as possible.

#include "state.h"
#include "config.h"

#include <M5Unified.h>
#include <esp_mac.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

#include <cstdio>
#include <cstring>
#include <ctime>
#include <climits>

namespace state {
namespace {

// ─── Internal state, protected by `mutex` ──────────────────────────────

struct AppState {
  Screen screen = Screen::Main;

  // Utilisation: current samples plus the previous values used to
  // derive the trend deltas. -1.0f means "no sample yet" throughout.
  float    sessionPct     = -1.0f;
  float    weeklyPct      = -1.0f;
  float    sessionDelta   = 0.0f;
  float    weeklyDelta    = 0.0f;
  TrendDir sessionTrend   = TrendDir::Flat;
  TrendDir weeklyTrend    = TrendDir::Flat;

  // Times.
  time_t sessionResetAt = 0;
  time_t weeklyResetAt  = 0;
  time_t lastSyncAt     = 0;
  time_t ntpLastSyncAt  = 0;

  // Daemon-reported status. Effective status (with the stale-age
  // override) is computed at snapshot time, not stored here.
  Status status = Status::Ok;

  // BLE / WiFi.
  BleState  ble      = BleState::Off;
  WifiState wifi     = WifiState::Off;
  int8_t    bleRssi  = INT8_MIN;
  int8_t    wifiRssi = INT8_MIN;
  char      wifiSsid[32] = {0};

  // Set once at init() and never mutated again.
  char deviceName[24] = {0};
  char macStr[18]     = {0};
  char firmware[12]   = {0};

  uint32_t version = 1;
};

AppState data;
SemaphoreHandle_t mutex = nullptr;

// RAII guard for the state mutex. Lock duration is short by design;
// we always copy values out and let callers do their work afterwards.
class Lock {
  SemaphoreHandle_t m_;
 public:
  explicit Lock(SemaphoreHandle_t m) : m_(m) {
    xSemaphoreTake(m_, portMAX_DELAY);
  }
  ~Lock() { xSemaphoreGive(m_); }
  Lock(const Lock&) = delete;
  Lock& operator=(const Lock&) = delete;
};

// Map a signed pp delta to an up/down/flat symbol, honouring the
// deadband from config so tiny fluctuations don't flicker the arrow.
TrendDir trendFromDelta(float delta) {
  if (delta >  cfg::TREND_DEADBAND_PP) return TrendDir::Up;
  if (delta < -cfg::TREND_DEADBAND_PP) return TrendDir::Down;
  return TrendDir::Flat;
}

// Read the ESP32's factory-burned Bluetooth MAC and format it into the
// "AA:BB:CC:DD:EE:FF" string that screen 3 displays for pairing.
void readMacIntoState() {
  uint8_t mac[6] = {0};
  if (esp_read_mac(mac, ESP_MAC_BT) == ESP_OK) {
    std::snprintf(data.macStr, sizeof(data.macStr),
                  "%02X:%02X:%02X:%02X:%02X:%02X",
                  mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  } else {
    std::strncpy(data.macStr, "??:??:??:??:??:??", sizeof(data.macStr) - 1);
    data.macStr[sizeof(data.macStr) - 1] = '\0';
  }
}

}  // anonymous namespace

// ─── Public API ────────────────────────────────────────────────────────

void init() {
  mutex = xSemaphoreCreateMutex();

  Lock lock(mutex);
  std::strncpy(data.deviceName, cfg::BLE_DEVICE_NAME,
               sizeof(data.deviceName) - 1);
  data.deviceName[sizeof(data.deviceName) - 1] = '\0';

  std::strncpy(data.firmware, cfg::FW_VERSION, sizeof(data.firmware) - 1);
  data.firmware[sizeof(data.firmware) - 1] = '\0';

  readMacIntoState();
}

Snapshot getSnapshot() {
  // Read hardware before locking. These calls are quick and have their
  // own internal synchronisation, so there's no need to hold our mutex
  // while they run.
  const int      batMv = M5.Power.getBatteryVoltage();
  const int      batLv = M5.Power.getBatteryLevel();
  const auto     chg   = M5.Power.isCharging();
  const uint32_t upSec = millis() / 1000;
  const time_t   now   = ::time(nullptr);

  Lock lock(mutex);

  Snapshot s{};
  s.screen = data.screen;

  s.sessionPct   = data.sessionPct;
  s.weeklyPct    = data.weeklyPct;
  s.sessionTrend = data.sessionTrend;
  s.weeklyTrend  = data.weeklyTrend;
  s.sessionDelta = data.sessionDelta;
  s.weeklyDelta  = data.weeklyDelta;

  s.sessionResetAt = data.sessionResetAt;
  s.weeklyResetAt  = data.weeklyResetAt;
  s.lastSyncAt     = data.lastSyncAt;
  s.ntpLastSyncAt  = data.ntpLastSyncAt;
  s.now            = now;

  // Effective status: promote Ok to Stale once the sync age exceeds
  // the threshold. Non-Ok statuses (Rate / Auth / Net) are problems
  // the daemon already flagged, so we leave them alone.
  if (data.lastSyncAt != 0 && data.status == Status::Ok &&
      (now - data.lastSyncAt) >
          static_cast<time_t>(cfg::SYNC_STALE_SEC)) {
    s.status = Status::Stale;
  } else {
    s.status = data.status;
  }

  s.ble      = data.ble;
  s.wifi     = data.wifi;
  s.bleRssi  = data.bleRssi;
  s.wifiRssi = data.wifiRssi;
  std::memcpy(s.wifiSsid,   data.wifiSsid,   sizeof(s.wifiSsid));
  std::memcpy(s.deviceName, data.deviceName, sizeof(s.deviceName));
  std::memcpy(s.macStr,     data.macStr,     sizeof(s.macStr));
  std::memcpy(s.firmware,   data.firmware,   sizeof(s.firmware));

  // Live readings, sourced before the lock.
  s.batteryVolts    = batMv > 0 ? batMv / 1000.0f : 0.0f;
  s.batteryPct      = batLv > 0 ? static_cast<uint8_t>(batLv) : 0;
  // M5Unified isCharging() returns an enum: is_unknown=-1,
  // is_discharging=0, is_charging=1. Compare to 1 explicitly so we
  // don't accidentally treat is_unknown as charging.
  s.batteryCharging = (static_cast<int8_t>(chg) == 1);
  s.uptimeSec       = upSec;

  s.version = data.version;
  return s;
}

void cycleScreen() {
  Lock lock(mutex);
  const uint8_t next = (static_cast<uint8_t>(data.screen) + 1) %
                       static_cast<uint8_t>(Screen::Count);
  data.screen = static_cast<Screen>(next);
  data.version++;
}

void setSample(float sessionPct, time_t sessionResetAt,
               float weeklyPct,  time_t weeklyResetAt,
               Status status,    time_t daemonTs) {
  Lock lock(mutex);

  // Trend deltas only mean something once we have a previous reading.
  // On the first sample, leave the trend flat and the delta zero.
  if (data.sessionPct >= 0.0f) {
    data.sessionDelta = sessionPct - data.sessionPct;
    data.sessionTrend = trendFromDelta(data.sessionDelta);
  } else {
    data.sessionDelta = 0.0f;
    data.sessionTrend = TrendDir::Flat;
  }
  if (data.weeklyPct >= 0.0f) {
    data.weeklyDelta = weeklyPct - data.weeklyPct;
    data.weeklyTrend = trendFromDelta(data.weeklyDelta);
  } else {
    data.weeklyDelta = 0.0f;
    data.weeklyTrend = TrendDir::Flat;
  }

  data.sessionPct     = sessionPct;
  data.weeklyPct      = weeklyPct;
  data.sessionResetAt = sessionResetAt;
  data.weeklyResetAt  = weeklyResetAt;
  data.status         = status;
  data.lastSyncAt     = daemonTs;
  data.version++;
}

void setBleState(BleState s) {
  Lock lock(mutex);
  if (data.ble == s) return;
  data.ble = s;
  // When dropping out of Connected, the cached RSSI becomes meaningless.
  if (s != BleState::Connected) data.bleRssi = INT8_MIN;
  data.version++;
}

void setBleRssi(int8_t rssi) {
  Lock lock(mutex);
  data.bleRssi = rssi;
  // Deliberately no version bump: RSSI ticks more often than the
  // display can usefully redraw.
}

void setWifiState(WifiState s) {
  Lock lock(mutex);
  if (data.wifi == s) return;
  data.wifi = s;
  if (s != WifiState::Online) {
    data.wifiRssi = INT8_MIN;
    data.wifiSsid[0] = '\0';
  }
  data.version++;
}

void setWifiInfo(const char* ssid, int8_t rssi) {
  Lock lock(mutex);
  if (ssid != nullptr) {
    std::strncpy(data.wifiSsid, ssid, sizeof(data.wifiSsid) - 1);
    data.wifiSsid[sizeof(data.wifiSsid) - 1] = '\0';
  }
  data.wifiRssi = rssi;
  data.version++;
}

void setNtpSynced() {
  Lock lock(mutex);
  data.ntpLastSyncAt = ::time(nullptr);
  data.version++;
}

}  // namespace state
