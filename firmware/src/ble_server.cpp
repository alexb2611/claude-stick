// claude-stick: BLE GATT server implementation
//
// NimBLE-Arduino 2.x peripheral. One custom service, two characteristics,
// and three callback classes (server-level, RX-write, TX-subscribe).
// Connection-state changes are pushed to state via the setters in
// state.h; payload bytes are handed straight to payload::parse.
//
// Security: just-works pairing with bonding. The first time a host
// pairs, NimBLE stores the keys in NVS; subsequent reconnects don't
// re-prompt. Fine for a desk-side device on a network you trust.

#include "ble_server.h"
#include "config.h"
#include "payload.h"
#include "state.h"

#include <Arduino.h>
#include <NimBLEDevice.h>

namespace ble_server {
namespace {

NimBLEServer*         server  = nullptr;
NimBLECharacteristic* rxChar  = nullptr;
NimBLECharacteristic* txChar  = nullptr;

// Set true on connect, consumed by the TX onSubscribe callback. Lets
// us fire the 0x01 refresh exactly once per session, immediately after
// the daemon has indicated it's listening, rather than blindly on
// connect (when it might not yet be subscribed).
bool wantRefreshOnSubscribe = false;

// Watchdog state. Advertising is supposed to resume automatically after
// a peer disconnects (we call startAdvertising() in onDisconnect), but
// in practice it sometimes doesn't: BlueZ-side keep-alives can hold
// the link past the supervision timeout, internal NimBLE state can get
// stuck, etc. tick() periodically asserts that whenever there's no
// peer connected, advertising is running.
constexpr uint32_t ADV_CHECK_INTERVAL_MS = 5000;
uint32_t lastAdvCheckMs = 0;
uint32_t advRestartCount = 0;
uint32_t lastHeartbeatMs = 0;
constexpr uint32_t HEARTBEAT_INTERVAL_MS = 60000;

// ─── Server-level callbacks (connect / disconnect) ─────────────────────

class ServerCallbacks final : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer* /*srv*/, NimBLEConnInfo& info) override {
    Serial.printf("BLE: peer connected (handle=%u)\n", info.getConnHandle());
    state::setBleState(state::BleState::Connected);
    wantRefreshOnSubscribe = true;
  }

  void onDisconnect(NimBLEServer* /*srv*/, NimBLEConnInfo& info,
                    int reason) override {
    Serial.printf("BLE: peer disconnected (handle=%u reason=0x%x)\n",
                  info.getConnHandle(), reason);
    state::setBleState(state::BleState::Advertising);
    // NimBLE auto-restarts advertising in most configurations, but
    // doing it explicitly here makes the behaviour obvious and resilient
    // to library defaults shifting between versions.
    NimBLEDevice::startAdvertising();
  }
};

// ─── RX characteristic (writes from the daemon) ────────────────────────

class RxCallbacks final : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic* chr,
               NimBLEConnInfo& /*info*/) override {
    const NimBLEAttValue& val = chr->getValue();
    Serial.printf("BLE: RX write, %u bytes\n",
                  static_cast<unsigned>(val.length()));
    if (val.length() > 0) {
      payload::parse(val.data(), val.length());
    }
  }
};

// ─── TX characteristic (notify + subscribe-driven refresh) ─────────────

class TxCallbacks final : public NimBLECharacteristicCallbacks {
  void onSubscribe(NimBLECharacteristic* chr, NimBLEConnInfo& /*info*/,
                   uint16_t subValue) override {
    // subValue != 0 means the peer has enabled notifications / indications.
    if (subValue != 0 && wantRefreshOnSubscribe) {
      Serial.println("BLE: peer subscribed; sending 0x01 refresh request");
      const uint8_t refreshByte = 0x01;
      chr->setValue(&refreshByte, 1);
      chr->notify();
      wantRefreshOnSubscribe = false;
    }
  }
};

ServerCallbacks serverCb;
RxCallbacks     rxCb;
TxCallbacks     txCb;

}  // anonymous namespace

// ─── Public API ────────────────────────────────────────────────────────

void init() {
  Serial.println("BLE: init starting");
  NimBLEDevice::init(cfg::BLE_DEVICE_NAME);
  NimBLEDevice::setPower(ESP_PWR_LVL_P9);     // max TX, fine indoors
  NimBLEDevice::setMTU(247);                  // allow large single writes

  // Just-works bonding: pair without PIN, but persist keys in NVS so
  // reconnects don't re-prompt. The host (Linux laptop) handles the
  // pairing dance the first time via bluetoothctl.
  NimBLEDevice::setSecurityAuth(true, false, false);
  NimBLEDevice::setSecurityIOCap(BLE_HS_IO_NO_INPUT_OUTPUT);

  server = NimBLEDevice::createServer();
  server->setCallbacks(&serverCb);

  NimBLEService* svc = server->createService(cfg::SVC_UUID);

  rxChar = svc->createCharacteristic(
      cfg::CHR_RX_UUID,
      NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR);
  rxChar->setCallbacks(&rxCb);

  txChar = svc->createCharacteristic(
      cfg::CHR_TX_UUID,
      NIMBLE_PROPERTY::NOTIFY);
  txChar->setCallbacks(&txCb);

  // NimBLE 2.x: services auto-start with the server; svc->start() is a
  // no-op and emits a deprecation warning. Start the server explicitly
  // so all characteristics are registered before advertising begins.
  server->start();

  // The main advertising packet has 31 bytes to play with. A 128-bit
  // service UUID (18 bytes) plus our 12-char name (14 bytes) plus the
  // flags AD (3 bytes) overflows that limit, and NimBLE silently drops
  // the name when it doesn't fit. That makes the stick invisible to
  // name-based scans, which is what bluetoothctl and bleak do by
  // default. So: name in the main packet, service UUID in the scan
  // response packet (which has its own 31 bytes).
  NimBLEAdvertisementData advData;
  advData.setFlags(0x06);  // general discoverable | BR/EDR not supported
  advData.setName(cfg::BLE_DEVICE_NAME);

  NimBLEAdvertisementData scanResp;
  scanResp.addServiceUUID(cfg::SVC_UUID);

  NimBLEAdvertising* adv = NimBLEDevice::getAdvertising();
  adv->setAdvertisementData(advData);
  adv->setScanResponseData(scanResp);
  adv->start();

  state::setBleState(state::BleState::Advertising);
  Serial.printf("BLE: advertising as %s\n", cfg::BLE_DEVICE_NAME);
}

void requestRefresh() {
  if (txChar == nullptr) return;
  const uint8_t refreshByte = 0x01;
  txChar->setValue(&refreshByte, 1);
  txChar->notify();
}

void tick() {
  const uint32_t now = millis();

  // Heartbeat log every minute showing what state we think we're in,
  // so if this thing ever breaks again we can see from the serial
  // monitor whether the stick believes it's connected, advertising,
  // or stuck somewhere in between.
  if (now - lastHeartbeatMs > HEARTBEAT_INTERVAL_MS) {
    lastHeartbeatMs = now;
    if (server != nullptr) {
      NimBLEAdvertising* adv = NimBLEDevice::getAdvertising();
      Serial.printf("BLE: heartbeat connected=%u advertising=%d restarts=%u\n",
                    static_cast<unsigned>(server->getConnectedCount()),
                    adv ? adv->isAdvertising() : -1,
                    advRestartCount);
    }
  }

  // Watchdog at a faster cadence: if no peer is connected and yet
  // advertising has stopped, kick it back on. Safe to call when already
  // advertising (no-op), so this is conservative.
  if (now - lastAdvCheckMs < ADV_CHECK_INTERVAL_MS) return;
  lastAdvCheckMs = now;

  if (server == nullptr) return;
  if (server->getConnectedCount() > 0) return;

  NimBLEAdvertising* adv = NimBLEDevice::getAdvertising();
  if (adv == nullptr) return;
  if (adv->isAdvertising()) return;

  advRestartCount++;
  Serial.printf("BLE: watchdog restarting advertising (#%u)\n",
                advRestartCount);
  adv->start();
}

}  // namespace ble_server
