// claude-stick: firmware-wide constants
// Every magic number used by the firmware lives here. Tweaking the visual
// design or runtime cadences should only require edits to this file.

#pragma once

#include <cstdint>

namespace cfg {

// ─── Display geometry (native landscape pixels, after rotation 3) ──────
// The panel is physically 135 wide × 240 tall; we rotate it so the screen
// reads as 240 wide × 135 tall, with the USB-C port on the right.

constexpr int16_t SCREEN_W = 240;
constexpr int16_t SCREEN_H = 135;
constexpr int16_t HEADER_H = 16;

// ─── Bar colour thresholds (utilisation percentage) ────────────────────
// Below TH_AMBER bars are green; from TH_AMBER up to TH_RED they're amber;
// at or above TH_RED they're red.

constexpr float TH_AMBER = 50.0f;
constexpr float TH_RED   = 80.0f;

// ─── Trend deadband ────────────────────────────────────────────────────
// Absolute pp delta below this is treated as "no change" for the trend
// glyph, so a flat reading doesn't flicker between up/down arrows on
// tiny fluctuations.

constexpr float TREND_DEADBAND_PP = 0.5f;

// ─── Sync staleness ────────────────────────────────────────────────────

constexpr uint32_t SYNC_STALE_SEC = 180;   // 3 min: status becomes "stale"
constexpr uint32_t SYNC_DEAD_SEC  = 600;   // 10 min: amber across the board

// ─── Tick cadences ─────────────────────────────────────────────────────

constexpr uint32_t DISPLAY_TICK_MS = 200;  // ~5 Hz refresh
constexpr uint32_t BUTTON_POLL_MS  = 50;   // ~20 Hz polling
constexpr uint32_t NTP_RESYNC_SEC  = 3600; // re-NTP every hour

// ─── NTP / Timezone ────────────────────────────────────────────────────
// POSIX TZ string for the UK: standard time is GMT (offset 0), daylight
// savings is BST starting last Sunday of March at 01:00 UTC and ending
// last Sunday of October. Edit if the stick lives elsewhere.

constexpr const char* TIMEZONE_TZ  = "GMT0BST,M3.5.0/1,M10.5.0";
constexpr const char* NTP_SERVER_1 = "pool.ntp.org";
constexpr const char* NTP_SERVER_2 = "time.cloudflare.com";

// ─── BLE service identification ────────────────────────────────────────
// Generated with `uuidgen`. Distinct from upstream clawdmeter so a host
// running both daemons can disambiguate.

constexpr const char* BLE_DEVICE_NAME = "claude-stick";

constexpr const char* SVC_UUID = "8e7c1a32-5b4d-4c2e-9e3f-1a2b3c4d5e6f";
constexpr const char* CHR_RX_UUID = "8e7c1a33-5b4d-4c2e-9e3f-1a2b3c4d5e6f";
constexpr const char* CHR_TX_UUID = "8e7c1a34-5b4d-4c2e-9e3f-1a2b3c4d5e6f";

// ─── Firmware version ──────────────────────────────────────────────────

constexpr const char* FW_VERSION = "0.1.0";

}  // namespace cfg
