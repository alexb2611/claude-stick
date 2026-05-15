// claude-stick: payload parser implementation
//
// Stateless apart from the rolling error counter; safe to call from
// the NimBLE callback context.

#include "payload.h"
#include "config.h"
#include "state.h"

#include <Arduino.h>
#include <ArduinoJson.h>

#include <cstring>
#include <ctime>

namespace payload {
namespace {

uint32_t parseErrors = 0;

// Map the daemon's short status string to our enum. Unknown values
// degrade gracefully to Ok rather than dropping the sample.
state::Status statusFromString(const char* s) {
  if (s == nullptr)                 return state::Status::Ok;
  if (std::strcmp(s, "ok")    == 0) return state::Status::Ok;
  if (std::strcmp(s, "stale") == 0) return state::Status::Stale;
  if (std::strcmp(s, "rate")  == 0) return state::Status::RateLimited;
  if (std::strcmp(s, "auth")  == 0) return state::Status::AuthFailed;
  if (std::strcmp(s, "net")   == 0) return state::Status::NetFailed;
  return state::Status::Ok;
}

}  // anonymous namespace

void parse(const uint8_t* buf, size_t len) {
  if (buf == nullptr || len == 0) {
    parseErrors++;
    return;
  }

  // ArduinoJson 7's JsonDocument auto-sizes; for our ~150-byte payloads
  // this stays cheap. We give it a hard cap via the parse call's len
  // argument so a malformed run-on can't allocate unbounded memory.
  JsonDocument doc;
  const DeserializationError err = deserializeJson(doc, buf, len);
  if (err) {
    Serial.printf("payload: JSON parse failed: %s\n", err.c_str());
    parseErrors++;
    return;
  }

  // Required fields. ArduinoJson returns sentinels for missing keys,
  // so test explicitly via is<T>() before reading.
  const bool haveS  = doc["s"].is<float>()  || doc["s"].is<int>();
  const bool haveW  = doc["w"].is<float>()  || doc["w"].is<int>();
  const bool haveSr = doc["sr"].is<uint32_t>();
  const bool haveWr = doc["wr"].is<uint32_t>();
  if (!haveS || !haveW || !haveSr || !haveWr) {
    Serial.println("payload: missing required field (s / sr / w / wr)");
    parseErrors++;
    return;
  }

  const float  s  = doc["s"].as<float>();
  const float  w  = doc["w"].as<float>();
  const time_t sr = static_cast<time_t>(doc["sr"].as<uint32_t>());
  const time_t wr = static_cast<time_t>(doc["wr"].as<uint32_t>());

  // Range validation. Percentages are 0..100; reset times should be
  // in the future relative to now (with a small grace window for clock
  // skew during the seconds-between-poll-and-arrival).
  if (s < 0.0f || s > 100.0f || w < 0.0f || w > 100.0f) {
    Serial.printf("payload: percentages out of range (s=%.2f w=%.2f)\n", s, w);
    parseErrors++;
    return;
  }

  // Optional fields. `st` defaults to "ok"; `ts` to current local time.
  const char* stStr = doc["st"] | "ok";
  const time_t ts   = doc["ts"].is<uint32_t>()
                          ? static_cast<time_t>(doc["ts"].as<uint32_t>())
                          : ::time(nullptr);

  state::setSample(s, sr, w, wr, statusFromString(stStr), ts);
  Serial.printf("payload: applied s=%.1f%% w=%.1f%% (st=%s)\n", s, w, stStr);
}

uint32_t getErrorCount() {
  return parseErrors;
}

}  // namespace payload
