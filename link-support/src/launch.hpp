// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>

namespace hector {
// Link shares start intent. Schedule local outputs at our launch boundary,
// including starts received from another peer. Do not modify the peer timeline.
class StartQuantizer {
  bool playing_ = false;
  int64_t request_ = 0;
  int64_t launch_ = 0;
public:
  template<class Session>
  int64_t update(const Session& state, double quantum) {
    const int64_t request = state.timeForIsPlaying().count();
    if (!state.isPlaying()) {
      playing_ = false;
      request_ = request;
      return launch_ = request;
    }
    if (!playing_ || request != request_) {
      const double beat = state.beatAtTime(std::chrono::microseconds(request), quantum);
      const double nearest = std::round(beat / quantum);
      // SDK time conversions round to microseconds. An already aligned start
      // must not be delayed by a whole extra quantum due to that rounding.
      const double tolerance = std::max(1e-9, state.tempo() * 2. / 60000000.);
      const double boundary = quantum * (std::abs(beat-nearest*quantum) <= tolerance
                                          ? nearest : std::ceil(beat/quantum));
      launch_ = std::max(request, state.timeAtBeat(boundary, quantum).count());
      request_ = request;
      playing_ = true;
    }
    // Latch the start once per intent, as for locally requested starts. Normal
    // timeline refreshes cannot re-arm it and produce another Reset pulse.
    return launch_;
  }
};
}
