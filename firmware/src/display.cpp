// claude-stick: display module implementation
//
// Pipeline per tick:
//   1. Rate-limit gate on cfg::DISPLAY_TICK_MS.
//   2. Pull a snapshot of state under its own mutex (we never lock here).
//   3. Clear the sprite, paint the header, dispatch to the active screen.
//   4. Push the sprite to the panel in one DMA blit.
//
// All coordinates are native landscape pixels (240 × 135).

#include "display.h"
#include "state.h"
#include "config.h"

#include <M5Unified.h>

#include <cstdio>
#include <cstring>
#include <ctime>
#include <climits>

namespace display {
namespace {

// ─── Colour palette ────────────────────────────────────────────────────
// Hex values match the mockup designs. RGB565 packed at compile time.

constexpr uint16_t rgb(uint8_t r, uint8_t g, uint8_t b) {
  return static_cast<uint16_t>(((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3));
}

constexpr uint16_t COL_BG       = rgb(0x0A, 0x0E, 0x14);
constexpr uint16_t COL_HEADER   = rgb(0x12, 0x16, 0x1F);
constexpr uint16_t COL_TEXT     = rgb(0xF1, 0xEF, 0xE8);
constexpr uint16_t COL_VALUE    = rgb(0xD3, 0xD1, 0xC7);
constexpr uint16_t COL_LABEL    = rgb(0xB4, 0xB2, 0xA9);
constexpr uint16_t COL_DIM      = rgb(0x88, 0x87, 0x80);
constexpr uint16_t COL_OK       = rgb(0x97, 0xC4, 0x59);
constexpr uint16_t COL_WARN     = rgb(0xEF, 0x9F, 0x27);
constexpr uint16_t COL_BAD      = rgb(0xE2, 0x4B, 0x4A);
constexpr uint16_t COL_ACCENT   = rgb(0x85, 0xB7, 0xEB);
constexpr uint16_t COL_TRACK    = rgb(0x1A, 0x1F, 0x2A);
constexpr uint16_t COL_DIVIDER  = rgb(0x25, 0x2A, 0x36);

// ─── Module-local state ────────────────────────────────────────────────

M5Canvas canvas(&M5.Display);
uint32_t lastTickMs  = 0;
bool     forceRedraw = true;

// ─── Helpers ───────────────────────────────────────────────────────────

uint16_t barColour(float pct) {
  if (pct >= cfg::TH_RED)   return COL_BAD;
  if (pct >= cfg::TH_AMBER) return COL_WARN;
  return COL_OK;
}

uint16_t statusColour(state::Status st) {
  switch (st) {
    case state::Status::Ok:           return COL_OK;
    case state::Status::Stale:
    case state::Status::RateLimited:  return COL_WARN;
    case state::Status::AuthFailed:
    case state::Status::NetFailed:    return COL_BAD;
  }
  return COL_DIM;
}

const char* statusLabel(state::Status st) {
  switch (st) {
    case state::Status::Ok:           return "OK";
    case state::Status::Stale:        return "Stale";
    case state::Status::RateLimited:  return "Rate";
    case state::Status::AuthFailed:   return "Auth";
    case state::Status::NetFailed:    return "Net";
  }
  return "?";
}

// Up-triangle, down-triangle, or horizontal dash. Centred on (cx, cy).
void drawTrend(int16_t cx, int16_t cy, state::TrendDir dir, uint16_t colour) {
  switch (dir) {
    case state::TrendDir::Up:
      canvas.fillTriangle(cx - 4, cy + 3, cx + 4, cy + 3, cx, cy - 4, colour);
      break;
    case state::TrendDir::Down:
      canvas.fillTriangle(cx - 4, cy - 3, cx + 4, cy - 3, cx, cy + 4, colour);
      break;
    case state::TrendDir::Flat:
      canvas.fillRect(cx - 4, cy - 1, 9, 2, colour);
      break;
  }
}

// "12s" / "42m" / "3h 12m" / "4d 6h" depending on magnitude.
void formatDuration(int32_t seconds, char* out, size_t cap) {
  if (seconds < 0) seconds = 0;
  if (seconds < 60) {
    snprintf(out, cap, "%lds", static_cast<long>(seconds));
  } else if (seconds < 3600) {
    snprintf(out, cap, "%ldm", static_cast<long>(seconds / 60));
  } else if (seconds < 86400) {
    snprintf(out, cap, "%ldh %ldm",
             static_cast<long>(seconds / 3600),
             static_cast<long>((seconds % 3600) / 60));
  } else {
    snprintf(out, cap, "%ldd %ldh",
             static_cast<long>(seconds / 86400),
             static_cast<long>((seconds % 86400) / 3600));
  }
}

// Same-day → "17:42"; otherwise → "Mon 09:00".
void formatReset(time_t when, time_t now, char* out, size_t cap) {
  struct tm wt{};
  struct tm nt{};
  localtime_r(&when, &wt);
  localtime_r(&now,  &nt);
  static const char* const dow[] = {
    "Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"
  };
  if (wt.tm_yday == nt.tm_yday && wt.tm_year == nt.tm_year) {
    snprintf(out, cap, "%02d:%02d", wt.tm_hour, wt.tm_min);
  } else {
    snprintf(out, cap, "%s %02d:%02d",
             dow[wt.tm_wday], wt.tm_hour, wt.tm_min);
  }
}

// ─── Header strip ──────────────────────────────────────────────────────
// 16 px tall. Always painted first regardless of which screen is active.

void drawHeader(const state::Snapshot& s) {
  canvas.fillRect(0, 0, cfg::SCREEN_W, cfg::HEADER_H, COL_HEADER);

  // BLE indicator: a dot whose colour reflects the connection state,
  // plus a short "BLE" label.
  uint16_t bleDot;
  switch (s.ble) {
    case state::BleState::Connected:   bleDot = COL_ACCENT; break;
    case state::BleState::Paired:      bleDot = COL_DIM;    break;
    case state::BleState::Advertising: bleDot = COL_WARN;   break;
    default:                           bleDot = COL_BAD;    break;
  }
  canvas.fillCircle(6, 8, 2, bleDot);

  canvas.setFont(&fonts::Font0);
  canvas.setTextSize(1);
  canvas.setTextColor(COL_ACCENT);
  canvas.setTextDatum(textdatum_t::middle_left);
  canvas.drawString("BLE", 12, 8);

  // Battery icon on the right: cell outline + fill + percentage text.
  uint16_t battCol = s.batteryPct < 15 ? COL_BAD
                   : s.batteryPct < 30 ? COL_WARN
                   :                     COL_OK;
  const int16_t iconX = cfg::SCREEN_W - 32;
  canvas.drawRect(iconX, 4, 14, 8, battCol);
  canvas.fillRect(iconX + 14, 6, 2, 4, battCol);
  const int16_t fillW = (s.batteryPct * 10) / 100;  // 0..10 px
  if (fillW > 0) canvas.fillRect(iconX + 2, 6, fillW, 4, battCol);

  char buf[8];
  snprintf(buf, sizeof(buf), "%u%%", static_cast<unsigned>(s.batteryPct));
  canvas.setTextColor(battCol);
  canvas.setTextDatum(textdatum_t::middle_right);
  canvas.drawString(buf, iconX - 2, 8);
}

// ─── Screen 1: Main (bars) ─────────────────────────────────────────────

void drawBarSection(int16_t topY,
                    const char* label,
                    float pct,
                    state::TrendDir trend,
                    int32_t resetSeconds) {
  // Section label (small, muted).
  canvas.setFont(&fonts::Font0);
  canvas.setTextSize(1);
  canvas.setTextColor(COL_LABEL);
  canvas.setTextDatum(textdatum_t::top_left);
  canvas.drawString(label, 8, topY);

  // Trend glyph, a touch left of the percentage.
  drawTrend(170, topY + 8, trend, COL_LABEL);

  // Big percentage, right-aligned. "--%" if we haven't seen data yet.
  canvas.setFont(&fonts::FreeSansBold12pt7b);
  canvas.setTextColor(COL_TEXT);
  canvas.setTextDatum(textdatum_t::top_right);
  char pctStr[8];
  if (pct < 0.0f) {
    snprintf(pctStr, sizeof(pctStr), "--");
  } else {
    snprintf(pctStr, sizeof(pctStr), "%d%%", static_cast<int>(pct + 0.5f));
  }
  canvas.drawString(pctStr, cfg::SCREEN_W - 8, topY - 3);

  // Bar: dark track behind, coloured fill in front.
  const int16_t barY = topY + 20;
  const int16_t barH = 10;
  const int16_t barW = cfg::SCREEN_W - 16;
  canvas.fillRoundRect(8, barY, barW, barH, 2, COL_TRACK);
  if (pct > 0.0f) {
    int16_t fillW = static_cast<int16_t>((pct / 100.0f) * barW);
    if (fillW < 2) fillW = 2;   // always show a sliver if non-zero
    if (fillW > barW) fillW = barW;
    canvas.fillRoundRect(8, barY, fillW, barH, 2, barColour(pct));
  }

  // Reset countdown.
  canvas.setFont(&fonts::Font0);
  canvas.setTextSize(1);
  canvas.setTextColor(COL_DIM);
  canvas.setTextDatum(textdatum_t::top_left);
  char dur[16];
  char reset[24];
  if (resetSeconds <= 0) {
    snprintf(reset, sizeof(reset), "resetting...");
  } else {
    formatDuration(resetSeconds, dur, sizeof(dur));
    snprintf(reset, sizeof(reset), "resets in %s", dur);
  }
  canvas.drawString(reset, 8, barY + barH + 3);
}

void drawMain(const state::Snapshot& s) {
  const int32_t sessLeft = static_cast<int32_t>(s.sessionResetAt - s.now);
  const int32_t weekLeft = static_cast<int32_t>(s.weeklyResetAt  - s.now);
  drawBarSection(22, "SESSION", s.sessionPct, s.sessionTrend, sessLeft);
  drawBarSection(72, "WEEKLY",  s.weeklyPct,  s.weeklyTrend,  weekLeft);
}

// ─── Screen 2: Detail ──────────────────────────────────────────────────

void drawDetailBlock(int16_t topY,
                     const char* label,
                     float pct,
                     state::TrendDir trend,
                     float delta,
                     time_t resetAt,
                     time_t now) {
  // Section label.
  canvas.setFont(&fonts::Font0);
  canvas.setTextSize(1);
  canvas.setTextColor(COL_LABEL);
  canvas.setTextDatum(textdatum_t::top_left);
  canvas.drawString(label, 8, topY);

  // Big percentage (one decimal). Left-aligned so the trend block can
  // sit immediately to its right.
  canvas.setFont(&fonts::FreeSansBold12pt7b);
  canvas.setTextColor(COL_TEXT);
  canvas.setTextDatum(textdatum_t::top_left);
  char pctStr[12];
  if (pct < 0.0f) {
    snprintf(pctStr, sizeof(pctStr), "--");
  } else {
    snprintf(pctStr, sizeof(pctStr), "%.1f%%", pct);
  }
  canvas.drawString(pctStr, 8, topY + 10);

  // Trend glyph + signed delta.
  drawTrend(130, topY + 18, trend, COL_LABEL);
  canvas.setFont(&fonts::Font0);
  canvas.setTextColor(COL_LABEL);
  canvas.setTextDatum(textdatum_t::middle_left);
  char deltaStr[12];
  snprintf(deltaStr, sizeof(deltaStr), "%+.1fpp", delta);
  canvas.drawString(deltaStr, 140, topY + 18);

  // "Resets <when>".
  canvas.setFont(&fonts::Font0);
  canvas.setTextColor(COL_DIM);
  canvas.setTextDatum(textdatum_t::top_left);
  canvas.drawString("Resets", 8, topY + 32);
  char reset[24];
  formatReset(resetAt, now, reset, sizeof(reset));
  canvas.setTextColor(COL_VALUE);
  canvas.drawString(reset, 48, topY + 32);
}

void drawDetail(const state::Snapshot& s) {
  drawDetailBlock(20, "SESSION", s.sessionPct, s.sessionTrend,
                  s.sessionDelta, s.sessionResetAt, s.now);

  canvas.drawFastHLine(8, 62, cfg::SCREEN_W - 16, COL_DIVIDER);

  drawDetailBlock(68, "WEEKLY", s.weeklyPct, s.weeklyTrend,
                  s.weeklyDelta, s.weeklyResetAt, s.now);

  canvas.drawFastHLine(8, 112, cfg::SCREEN_W - 16, COL_DIVIDER);

  // Footer: sync age on the left, status indicator on the right.
  canvas.setFont(&fonts::Font0);
  canvas.setTextSize(1);
  canvas.setTextColor(COL_DIM);
  canvas.setTextDatum(textdatum_t::top_left);
  canvas.drawString("Sync", 8, 120);

  char syncStr[20];
  if (s.lastSyncAt == 0) {
    snprintf(syncStr, sizeof(syncStr), "--");
  } else {
    char dur[12];
    formatDuration(static_cast<int32_t>(s.now - s.lastSyncAt),
                   dur, sizeof(dur));
    snprintf(syncStr, sizeof(syncStr), "%s ago", dur);
  }
  canvas.setTextColor(COL_VALUE);
  canvas.drawString(syncStr, 36, 120);

  const uint16_t stCol = statusColour(s.status);
  canvas.fillCircle(140, 124, 2, stCol);
  canvas.setTextColor(COL_DIM);
  canvas.drawString("Status", 148, 120);
  canvas.setTextColor(stCol);
  canvas.drawString(statusLabel(s.status), 192, 120);
}

// ─── Screen 3: Connection ──────────────────────────────────────────────

void drawRow(int16_t y, const char* label, const char* value) {
  canvas.setFont(&fonts::Font0);
  canvas.setTextSize(1);
  canvas.setTextColor(COL_DIM);
  canvas.setTextDatum(textdatum_t::top_left);
  canvas.drawString(label, 8, y);
  canvas.setTextColor(COL_VALUE);
  canvas.drawString(value, 72, y);
}

void drawConnection(const state::Snapshot& s) {
  // Device name (larger, bold).
  canvas.setFont(&fonts::FreeSansBold9pt7b);
  canvas.setTextColor(COL_TEXT);
  canvas.setTextDatum(textdatum_t::top_left);
  canvas.drawString(s.deviceName, 8, 20);

  // MAC address in accent blue.
  canvas.setFont(&fonts::Font0);
  canvas.setTextSize(1);
  canvas.setTextColor(COL_ACCENT);
  canvas.drawString(s.macStr, 8, 38);

  canvas.drawFastHLine(8, 50, cfg::SCREEN_W - 16, COL_DIVIDER);

  char buf[40];

  // BLE row.
  switch (s.ble) {
    case state::BleState::Connected:
      if (s.bleRssi > INT8_MIN)
        snprintf(buf, sizeof(buf), "Connected %d dBm", s.bleRssi);
      else
        snprintf(buf, sizeof(buf), "Connected");
      break;
    case state::BleState::Paired:      snprintf(buf, sizeof(buf), "Paired");      break;
    case state::BleState::Advertising: snprintf(buf, sizeof(buf), "Advertising"); break;
    default:                           snprintf(buf, sizeof(buf), "Off");         break;
  }
  drawRow(58, "BLE", buf);

  // WiFi row.
  switch (s.wifi) {
    case state::WifiState::Online:
      if (s.wifiRssi > INT8_MIN)
        snprintf(buf, sizeof(buf), "%s %d dBm", s.wifiSsid, s.wifiRssi);
      else
        snprintf(buf, sizeof(buf), "%s", s.wifiSsid);
      break;
    case state::WifiState::Connecting: snprintf(buf, sizeof(buf), "Connecting"); break;
    case state::WifiState::Failed:     snprintf(buf, sizeof(buf), "Failed");     break;
    default:                           snprintf(buf, sizeof(buf), "Offline");    break;
  }
  drawRow(70, "WiFi", buf);

  // NTP row.
  if (s.ntpLastSyncAt == 0) {
    snprintf(buf, sizeof(buf), "not synced");
  } else {
    char dur[12];
    formatDuration(static_cast<int32_t>(s.now - s.ntpLastSyncAt),
                   dur, sizeof(dur));
    snprintf(buf, sizeof(buf), "synced %s ago", dur);
  }
  drawRow(82, "NTP", buf);

  // Battery row.
  snprintf(buf, sizeof(buf), "%.2fV %s",
           s.batteryVolts,
           s.batteryCharging ? "charging" : "discharging");
  drawRow(94, "Battery", buf);

  // Uptime row.
  char up[16];
  formatDuration(static_cast<int32_t>(s.uptimeSec), up, sizeof(up));
  drawRow(106, "Uptime", up);

  // Firmware row.
  drawRow(118, "Firmware", s.firmware);
}

}  // anonymous namespace

// ─── Public API ────────────────────────────────────────────────────────

void init() {
  // Rotation 3 puts the panel in landscape with USB-C on the right.
  M5.Display.setRotation(3);
  M5.Display.fillScreen(COL_BG);

  canvas.setColorDepth(16);
  canvas.createSprite(cfg::SCREEN_W, cfg::SCREEN_H);
  canvas.setTextWrap(false);
  forceRedraw = true;
}

void invalidate() {
  forceRedraw = true;
}

void tick() {
  const uint32_t nowMs = millis();
  if (!forceRedraw && (nowMs - lastTickMs) < cfg::DISPLAY_TICK_MS) {
    return;
  }
  lastTickMs = nowMs;
  forceRedraw = false;

  const state::Snapshot s = state::getSnapshot();

  canvas.fillSprite(COL_BG);
  drawHeader(s);

  switch (s.screen) {
    case state::Screen::Main:       drawMain(s);       break;
    case state::Screen::Detail:     drawDetail(s);     break;
    case state::Screen::Connection: drawConnection(s); break;
    default:                        drawMain(s);       break;
  }

  canvas.pushSprite(0, 0);
}

}  // namespace display
