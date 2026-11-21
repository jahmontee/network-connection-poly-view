// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <array>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <string>
namespace hector {
struct Selection {
  std::string peer, stream;
  bool operator==(const Selection &other) const {
    return peer == other.peer && stream == other.stream;
  }
  bool valid() const {
    return peer.size() <= 256 && stream.size() <= 256 &&
           peer.find('\0') == std::string::npos &&
           stream.find('\0') == std::string::npos &&
           peer.empty() == stream.empty();
  }
};
// Used only by non-realtime callers. Atomic words avoid a data race while a
// bounded seqlock snapshot is being retried. No string allocation occurs in
// run().
struct AtomicSelection {
  std::atomic<uint64_t> version{0};
  std::array<std::atomic<uint64_t>, 66> words{};
  bool store(const Selection &value) {
    if (!value.valid())
      return false;
    auto seq = version.load(std::memory_order_acquire);
    if ((seq & 1) || !version.compare_exchange_strong(
                         seq, seq + 1, std::memory_order_acq_rel))
      return false;
    std::array<char, 528> data{};
    std::memcpy(data.data(), value.peer.data(), value.peer.size());
    std::memcpy(data.data() + 264, value.stream.data(), value.stream.size());
    for (unsigned i = 0; i < 66; ++i) {
      uint64_t word;
      std::memcpy(&word, data.data() + i * 8, 8);
      words[i].store(word, std::memory_order_relaxed);
    }
    version.store(seq + 2, std::memory_order_release);
    return true;
  }
  bool load(Selection &value) const {
    for (unsigned attempt = 0; attempt < 4; ++attempt) {
      const auto seq = version.load(std::memory_order_acquire);
      if (seq & 1)
        continue;
      std::array<char, 528> data{};
      for (unsigned i = 0; i < 66; ++i) {
        auto word = words[i].load(std::memory_order_relaxed);
        std::memcpy(data.data() + i * 8, &word, 8);
      }
      std::atomic_thread_fence(std::memory_order_acquire);
      if (seq != version.load(std::memory_order_relaxed))
        continue;
      if (data[256] || data[520])
        return false;
      Selection next{
          std::string(data.data(), strnlen(data.data(), 257)),
          std::string(data.data() + 264, strnlen(data.data() + 264, 257))};
      if (!next.valid())
        return false;
      value = std::move(next);
      return true;
    }
    return false;
  }
};
} // namespace hector
