// claude-stick: firmware entry point
//
// Brings up M5Unified, the state module, the display, WiFi+NTP, and
// the BLE GATT server, then loops the display and time-sync ticks.
// Any of the three buttons cycles to the next screen.

#include <Arduino.h>
#include <M5Unified.h>

#include <ctime>

#include "config.h"
#include "display.h"
#include "state.h"
#include "ble_server.h"
#include "wifi_time.h"

void setup() {
  auto cfgM5 = M5.config();
  M5.begin(cfgM5);

  // M5Unified's M5.begin() doesn't initialise Arduino Serial; NimBLE and
  // M5GFX log to UART via ESP_LOG directly, so they show up regardless,
  // but our own Serial.print calls would silently vanish without this.
  Serial.begin(115200);

  state::init();
  display::init();
  wifi_time::init();      // seeds clock from RTC + kicks off WiFi/NTP
  ble_server::init();

#ifdef DEMO_DATA
  // Temporary visual smoke-test: pretend the BLE daemon, WiFi and NTP
  // have all come up so the screens look populated. Remove the
  // `-D DEMO_DATA` build flag once the real input modules land.
  const time_t now = ::time(nullptr);
  state::setSample(42.3f, now + 3 * 3600 + 12 * 60,
                   67.2f, now + 4 * 86400 + 6 * 3600,
                   state::Status::Ok, now - 14);
  state::setBleState(state::BleState::Connected);
  state::setBleRssi(-52);
  state::setWifiState(state::WifiState::Online);
  state::setWifiInfo("home-2g", -64);
  state::setNtpSynced();
#endif
}

void loop() {
  M5.update();

  if (M5.BtnA.wasPressed() || M5.BtnB.wasPressed() || M5.BtnPWR.wasPressed()) {
    state::cycleScreen();
    display::invalidate();
  }

  display::tick();
  wifi_time::tick();
  ble_server::tick();
  delay(10);
}
