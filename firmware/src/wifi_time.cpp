// claude-stick: WiFi + NTP implementation
//
// State machine, all transitions driven by tick() at ~1 Hz:
//
//   Boot ──> Connecting ──> AwaitNtp ──> Synced
//                ↓              ↓           │
//                └──> RetryWait <┘           │
//                       ↑                    │ (every NTP_RESYNC_SEC)
//                       └────────────────────┘
//
// Boot phase reads the battery-backed RTC; if it holds a plausible
// time (year >= 2024) we settimeofday() from it so the rest of the
// firmware has a sensible clock immediately, even before WiFi comes
// up. Once NTP completes we write the network time back to the RTC,
// so a power cycle on a flat-network day still boots with a clock
// only seconds off rather than at the build epoch.
//
// Always-on WiFi: the M5StickC Plus's BLE and WiFi share the 2.4 GHz
// radio via ESP32's built-in coexistence. Battery cost is small for
// a USB-powered desk meter and the alternative (cycle WiFi every hour)
// would lose us live RSSI on screen 3.

#include "wifi_time.h"
#include "config.h"
#include "state.h"

// Friendly error if the user hasn't created secrets.h yet.
#if __has_include("secrets.h")
  #include "secrets.h"
#else
  #error "Missing firmware/src/secrets.h. Copy secrets.h.example to secrets.h and fill in your WiFi credentials."
#endif

#include <Arduino.h>
#include <M5Unified.h>
#include <WiFi.h>
#include <esp_sntp.h>

#include <ctime>
#include <cstring>
#include <sys/time.h>

namespace wifi_time {
namespace {

enum class Phase : uint8_t {
  Boot,
  Connecting,
  AwaitNtp,
  Synced,
  RetryWait,
};

Phase    phase            = Phase::Boot;
uint32_t phaseEnteredMs   = 0;
uint32_t lastTickMs       = 0;
uint32_t lastSyncedSec    = 0;   // millis()/1000 when we entered Synced
uint32_t lastRssiUpdateMs = 0;

constexpr uint32_t CONNECT_TIMEOUT_MS = 30000;
constexpr uint32_t NTP_TIMEOUT_MS     = 15000;
constexpr uint32_t RETRY_BACKOFF_MS   = 60000;
constexpr uint32_t TICK_MIN_MS        = 1000;
constexpr uint32_t RSSI_REFRESH_MS    = 5000;

// Sentinel: any epoch beyond this means the clock has been set to a
// real wall-clock value (not just the build/default).
constexpr time_t   EPOCH_2024_01_01   = 1704067200;

void enterPhase(Phase p) {
  phase = p;
  phaseEnteredMs = millis();
}

// mktime() interprets its input as local time; we need a UTC-tm to
// epoch conversion. timegm() isn't reliably present in every ESP-IDF
// newlib build, so use the classic TZ-swap trick instead.
time_t utcMktime(struct tm* tm_in) {
  char saved[64] = {0};
  const char* cur = getenv("TZ");
  if (cur) {
    std::strncpy(saved, cur, sizeof(saved) - 1);
  }
  setenv("TZ", "UTC0", 1);
  tzset();
  tm_in->tm_isdst = 0;
  const time_t epoch = mktime(tm_in);
  if (saved[0]) {
    setenv("TZ", saved, 1);
  } else {
    unsetenv("TZ");
  }
  tzset();
  return epoch;
}

bool seedFromRtc() {
  m5::rtc_datetime_t dt = M5.Rtc.getDateTime();
  if (dt.date.year < 2024) return false;
  struct tm tm_in{};
  tm_in.tm_year = dt.date.year - 1900;
  tm_in.tm_mon  = dt.date.month - 1;
  tm_in.tm_mday = dt.date.date;
  tm_in.tm_hour = dt.time.hours;
  tm_in.tm_min  = dt.time.minutes;
  tm_in.tm_sec  = dt.time.seconds;
  const time_t epoch = utcMktime(&tm_in);
  if (epoch < EPOCH_2024_01_01) return false;
  const struct timeval tv = { epoch, 0 };
  settimeofday(&tv, nullptr);
  return true;
}

void writeRtc() {
  const time_t now = time(nullptr);
  if (now < EPOCH_2024_01_01) return;
  struct tm tm_now;
  gmtime_r(&now, &tm_now);
  m5::rtc_datetime_t dt;
  dt.date.year    = tm_now.tm_year + 1900;
  dt.date.month   = tm_now.tm_mon + 1;
  dt.date.date    = tm_now.tm_mday;
  dt.date.weekDay = tm_now.tm_wday;
  dt.time.hours   = tm_now.tm_hour;
  dt.time.minutes = tm_now.tm_min;
  dt.time.seconds = tm_now.tm_sec;
  M5.Rtc.setDateTime(dt);
}

void startConnect() {
  state::setWifiState(state::WifiState::Connecting);
  WiFi.persistent(false);    // don't churn flash storing creds
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(true);       // modem-sleep; BLE coexistence prefers it
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  enterPhase(Phase::Connecting);
  Serial.printf("wifi_time: connecting to %s\n", WIFI_SSID);
}

void startNtp() {
  // configTime() spins up the SNTP daemon in the background and
  // updates the system clock when packets arrive. UTC offsets are 0
  // here because timezone display is handled via the TZ env var that
  // init() has already set.
  configTime(0, 0, cfg::NTP_SERVER_1, cfg::NTP_SERVER_2);
  enterPhase(Phase::AwaitNtp);
  Serial.println("wifi_time: NTP sync starting");
}

bool isNtpComplete() {
  if (sntp_get_sync_status() == SNTP_SYNC_STATUS_COMPLETED) return true;
  // SNTP_SYNC_STATUS_RESET clears once consumed; checking for a real
  // wall-clock value catches the race where status was already read.
  return time(nullptr) > EPOCH_2024_01_01;
}

}  // anonymous namespace

void init() {
  setenv("TZ", cfg::TIMEZONE_TZ, 1);
  tzset();

  if (seedFromRtc()) {
    const time_t t = time(nullptr);
    Serial.printf("wifi_time: seeded from RTC, epoch %lld\n", (long long)t);
  } else {
    Serial.println("wifi_time: RTC blank or pre-2024, relying on NTP");
  }

  state::setWifiState(state::WifiState::Off);
  startConnect();
}

void tick() {
  const uint32_t now = millis();
  if (now - lastTickMs < TICK_MIN_MS) return;
  lastTickMs = now;

  switch (phase) {
    case Phase::Boot:
      // init() should have moved us past this; defensive only.
      startConnect();
      break;

    case Phase::Connecting:
      if (WiFi.status() == WL_CONNECTED) {
        state::setWifiState(state::WifiState::Online);
        state::setWifiInfo(WIFI_SSID, WiFi.RSSI());
        Serial.printf("wifi_time: connected, RSSI %d dBm, IP %s\n",
                      WiFi.RSSI(), WiFi.localIP().toString().c_str());
        startNtp();
      } else if (now - phaseEnteredMs > CONNECT_TIMEOUT_MS) {
        state::setWifiState(state::WifiState::Failed);
        Serial.println("wifi_time: connect timeout, will retry");
        enterPhase(Phase::RetryWait);
      }
      break;

    case Phase::AwaitNtp:
      if (isNtpComplete()) {
        writeRtc();
        state::setNtpSynced();
        lastSyncedSec = now / 1000;
        const time_t t = time(nullptr);
        Serial.printf("wifi_time: NTP synced, epoch %lld\n", (long long)t);
        enterPhase(Phase::Synced);
      } else if (now - phaseEnteredMs > NTP_TIMEOUT_MS) {
        Serial.println("wifi_time: NTP timeout, will retry");
        enterPhase(Phase::RetryWait);
      }
      break;

    case Phase::Synced:
      if (now - lastRssiUpdateMs > RSSI_REFRESH_MS) {
        if (WiFi.status() == WL_CONNECTED) {
          state::setWifiInfo(WIFI_SSID, WiFi.RSSI());
        } else {
          Serial.println("wifi_time: connection dropped");
          state::setWifiState(state::WifiState::Connecting);
          enterPhase(Phase::Connecting);
        }
        lastRssiUpdateMs = now;
      }
      if ((now / 1000) - lastSyncedSec > cfg::NTP_RESYNC_SEC) {
        startNtp();   // returns us to AwaitNtp; old time stays valid meanwhile
      }
      break;

    case Phase::RetryWait:
      if (now - phaseEnteredMs > RETRY_BACKOFF_MS) {
        WiFi.disconnect(true);
        startConnect();
      }
      break;
  }
}

}  // namespace wifi_time
