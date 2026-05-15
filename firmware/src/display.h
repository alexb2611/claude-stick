// claude-stick: display module
//
// Owns the LovyanGFX sprite that double-buffers the panel and turns the
// current state::Snapshot into pixels for the three screens we sketched.
// All public functions are safe to call from the Arduino loop context;
// none of them grab state's mutex directly. They go through
// state::getSnapshot() instead.

#pragma once

namespace display {

// Configure the panel (rotation, sprite buffer) and prepare for the
// first paint. Call once during setup() after M5.begin().
void init();

// Drive the display. Call every loop() iteration; internally rate-limits
// to cfg::DISPLAY_TICK_MS so it doesn't burn CPU on no-op redraws.
void tick();

// Force a full repaint on the next tick(). Useful after coming back
// from a power-saving sleep, or when something off-screen has changed
// the colour palette.
void invalidate();

}  // namespace display
