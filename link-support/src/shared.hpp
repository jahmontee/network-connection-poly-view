// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "selection.hpp"
#include <algorithm>
#include <array>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <cstddef>
#include <fcntl.h>
#include <new>
#include <stdexcept>
#include <string>
#include <sys/file.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

namespace hector {
constexpr uint64_t kMagic = 0x484543544c4e4b31ULL;
// Version 3 requires kernel-managed client leases. Reject older clients/helpers
// rather than permitting an old atomic-only client to bypass the lease.
constexpr uint32_t kVersion = 4, kFrames = 1024, kSlots = 128;
constexpr const char *kDefaultSession = "/run/hector-link/session";
constexpr const char *kSyncUri = "urn:hector-community:link:sync";
constexpr const char *kAudioUri = "urn:hector-community:link:audio";
static_assert(std::atomic<uint64_t>::is_always_lock_free);
inline int64_t nowUs() {
  // Match Ableton's Linux platform clock, not std::steady_clock (MONOTONIC).
  timespec stamp{};
  clock_gettime(CLOCK_MONOTONIC_RAW, &stamp);
  return int64_t(stamp.tv_sec) * 1000000 + stamp.tv_nsec / 1000;
}
inline uint64_t bits(double value) {
  uint64_t b;
  std::memcpy(&b, &value, 8);
  return b;
}
inline double number(uint64_t b) {
  double v;
  std::memcpy(&v, &b, 8);
  return v;
}
inline double cleanNumber(double v, double fallback = 0) {
  return std::isfinite(v) ? v : fallback;
}
struct Packet {
  int64_t beginUs = 0, endUs = 0;
  uint64_t generation = 0;
  uint32_t frames = 0, rate = 0;
  std::array<float, kFrames * 2> samples{};
};
template <class T, uint32_t N> struct alignas(64) Ring {
  alignas(64) std::atomic<uint64_t> write{0};
  alignas(64) std::atomic<uint64_t> read{0};
  std::array<T, N> slots{};
  T *reserve() {
    const auto w = write.load(std::memory_order_relaxed);
    return w - read.load(std::memory_order_acquire) < N ? &slots[w % N]
                                                        : nullptr;
  }
  void commit() { write.fetch_add(1, std::memory_order_release); }
  const T *front() const {
    const auto r = read.load(std::memory_order_relaxed);
    return r < write.load(std::memory_order_acquire) ? &slots[r % N] : nullptr;
  }
  void pop() { read.fetch_add(1, std::memory_order_release); }
};
struct Timeline {
  int64_t time = 0;
  double beat = 0, bpm = 120, quantum = 4;
  bool playing = false;
  uint64_t peers = 0;
  int64_t transportTime = 0;
};
struct alignas(64) Shared {
  uint64_t magic = kMagic;
  uint32_t version = kVersion, bytes = sizeof(Shared);
  std::atomic<uint64_t> ready{0}, heartbeat{0}, syncOwner{0}, audioOwner{0};
  std::atomic<uint64_t> syncRunHeartbeat{0};
  // Written once by the helper before its first release-published heartbeat.
  std::array<char, 16> interfaceName{};
  std::atomic<uint64_t> sequence{0}, time{0}, beat{0}, bpm{bits(120)},
      quantum{bits(4)}, playing{0}, peers{0};
  std::atomic<uint64_t> transportTime{0};
  std::atomic<uint64_t> enable{0}, audio{0}, startStop{0}, requestedTempo{0},
      requestedTransport{0};
  std::atomic<uint64_t> requestedQuantum{bits(4)}, receive{0};
  std::atomic<uint64_t> selectedId{0}, selectedAvailable{0};
  std::atomic<uint64_t> receiveGeneration{0};
  AtomicSelection selection;
  std::atomic<uint64_t> sent{0}, received{0}, txOverflow{0}, rxOverflow{0},
      underrun{0}, malformed{0};
  Ring<Packet, kSlots> tx, rx;
  void publish(const Timeline &s) {
    sequence.fetch_add(1, std::memory_order_acq_rel);
    time.store(s.time, std::memory_order_relaxed);
    beat.store(bits(s.beat), std::memory_order_relaxed);
    bpm.store(bits(s.bpm), std::memory_order_relaxed);
    quantum.store(bits(s.quantum), std::memory_order_relaxed);
    playing.store(s.playing, std::memory_order_relaxed);
    peers.store(s.peers, std::memory_order_relaxed);
    transportTime.store(s.transportTime, std::memory_order_relaxed);
    sequence.fetch_add(1, std::memory_order_release);
  }
  bool snapshot(Timeline &s) const {
    for (int i = 0; i < 3; ++i) {
      const auto first = sequence.load(std::memory_order_acquire);
      if (first & 1)
        continue;
      Timeline next{
          static_cast<int64_t>(time.load(std::memory_order_relaxed)),
          number(beat.load(std::memory_order_relaxed)),
          number(bpm.load(std::memory_order_relaxed)),
          number(quantum.load(std::memory_order_relaxed)),
          bool(playing.load(std::memory_order_relaxed)),
          peers.load(std::memory_order_relaxed),
          static_cast<int64_t>(transportTime.load(std::memory_order_relaxed))};
      std::atomic_thread_fence(std::memory_order_acquire);
      if (first == sequence.load(std::memory_order_relaxed)) {
        s = next;
        return true;
      }
    }
    return false;
  }
};
class Mapping {
  int fd_ = -1;
  Shared *ptr_ = nullptr;

public:
  Mapping(const std::string &path, bool create = false) {
    fd_ = open(path.c_str(),
               O_RDWR | O_CLOEXEC | O_NOFOLLOW |
                   (create ? (O_CREAT | O_EXCL) : 0),
               0600);
    if (fd_ < 0)
      throw std::runtime_error("Cannot open session file: " + path);
    struct stat st{};
    if (fstat(fd_, &st) || !S_ISREG(st.st_mode) || st.st_uid != getuid() ||
        (st.st_mode & 0077) || st.st_nlink != 1) {
      close(fd_);
      throw std::runtime_error("Session must be a private, owned regular file");
    }
    if ((create && ftruncate(fd_, sizeof(Shared))) ||
        (!create && st.st_size != sizeof(Shared))) {
      close(fd_);
      throw std::runtime_error("Session size mismatch");
    }
    void *p = mmap(nullptr, sizeof(Shared), PROT_READ | PROT_WRITE, MAP_SHARED,
                   fd_, 0);
    if (p == MAP_FAILED) {
      close(fd_);
      throw std::runtime_error("Cannot map session");
    }
    ptr_ = static_cast<Shared *>(p);
    if (create) {
      if (flock(fd_, LOCK_EX | LOCK_NB)) {
        munmap(ptr_, sizeof(Shared));
        close(fd_);
        throw std::runtime_error("Session helper lock unavailable");
      }
      new (ptr_) Shared;
      ptr_->ready.store(1, std::memory_order_release);
    } else if (ptr_->ready.load(std::memory_order_acquire) != 1 ||
               ptr_->magic != kMagic || ptr_->version != kVersion ||
               ptr_->bytes != sizeof(Shared)) {
      munmap(ptr_, sizeof(Shared));
      close(fd_);
      ptr_ = nullptr;
      throw std::runtime_error("Session ABI mismatch");
    }
  }
  Mapping(const Mapping &) = delete;
  Mapping &operator=(const Mapping &) = delete;
  ~Mapping() {
    if (ptr_)
      munmap(ptr_, sizeof(Shared));
    if (fd_ >= 0)
      close(fd_);
  }
  Shared &get() { return *ptr_; }
  bool claimClient(bool audio, uint64_t token) {
    // OFD byte-range locks are per open description, so even two clients in
    // one engine cannot both acquire a role. The kernel releases the lease
    // after an abrupt client process exit; a stale shared token is then safe
    // to replace. Helper flock and client byte-range locks are independent.
    struct flock lease{};
    lease.l_type = F_WRLCK;
    lease.l_whence = SEEK_SET;
    lease.l_start = audio ? offsetof(Shared, audioOwner)
                          : offsetof(Shared, syncOwner);
    lease.l_len = sizeof(uint64_t);
    if (fcntl(fd_, F_OFD_SETLK, &lease)) {
      if (errno == EAGAIN || errno == EACCES)
        return false;
      throw std::runtime_error("Kernel client lease unavailable");
    }
    (audio ? ptr_->audioOwner : ptr_->syncOwner).store(token);
    if (!audio)
      ptr_->syncRunHeartbeat.store(0);
    return true;
  }
  // Observer mappings only: query through a different open description from
  // the owner. This reports attachment, not evidence of audio processing.
  bool clientAttached(bool audio = false) const {
    struct flock lease{};
    lease.l_type = F_WRLCK;
    lease.l_whence = SEEK_SET;
    lease.l_start = audio ? offsetof(Shared, audioOwner)
                          : offsetof(Shared, syncOwner);
    lease.l_len = sizeof(uint64_t);
    if (fcntl(fd_, F_OFD_GETLK, &lease))
      throw std::runtime_error("Cannot inspect client lease");
    return lease.l_type != F_UNLCK;
  }
  bool isCurrent(const std::string &path) const {
    struct stat current{}, original{};
    return !lstat(path.c_str(), &current) && !fstat(fd_, &original) &&
           S_ISREG(current.st_mode) && current.st_dev == original.st_dev &&
           current.st_ino == original.st_ino;
  }
  static bool removeStale(const std::string &path) {
    struct stat before{}, current{};
    if (lstat(path.c_str(), &before))
      return errno == ENOENT;
    Mapping old(path);
    if (flock(old.fd_, LOCK_EX | LOCK_NB))
      return false;
    if (old.get().heartbeat.load() > uint64_t(nowUs() - 1000000))
      return false;
    if (lstat(path.c_str(), &current) || current.st_ino != before.st_ino ||
        current.st_dev != before.st_dev)
      return false;
    return unlink(path.c_str()) == 0;
  }
};
inline double beatAt(const Timeline &s, int64_t t) {
  return s.beat + (t - s.time) * s.bpm / 60000000.;
}
// Callback jitter is filtered against the audio frame counter. This remains an
// estimate until the host supplies its actual presentation timestamp/latency.
class SampleClock {
  double time_ = 0;
  bool initialized_ = false;

public:
  int64_t advance(uint32_t frames, double rate) {
    const double now = nowUs();
    if (!initialized_ || std::abs(now - time_) > 100000) {
      time_ = now;
      initialized_ = true;
    } else
      time_ += (now - time_) * 0.01;
    const auto result = static_cast<int64_t>(time_);
    time_ += frames * 1000000. / rate;
    return result;
  }
};
inline int16_t toInt16(float f) {
  const float v = std::clamp(static_cast<float>(cleanNumber(f)), -1.f, 1.f);
  return static_cast<int16_t>(std::lrint(v * (v < 0 ? 32768.f : 32767.f)));
}
inline float fromInt16(int16_t v) {
  return float(v) / (v < 0 ? 32768.f : 32767.f);
}
} // namespace hector
