// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "shared.hpp"
#include <memory>
#include <mutex>
#include <thread>
namespace hector {
// File operations, reconnects and preference copies stay on this worker. run()
// pins only a lock-free pointer; retired mappings are reclaimed after readers
// exit.
class Connector {
  bool audio_;
  std::string path_;
  uint64_t token_;
  std::atomic<Shared *> current_{nullptr};
  std::atomic<uint64_t> readers_{0}, epoch_{0};
  std::atomic<bool> stop_{false};
  std::atomic<bool> preferencesPending_{false};
  std::unique_ptr<Mapping> map_;
  std::thread worker_;
  std::mutex preferences_;
  Selection desired_;
  uint64_t restoreSerial_ = 0, appliedSerial_ = 0;
  bool connectedBefore_ = false;
  void synchronize(Shared &s) {
    if (!audio_)
      return;
    std::lock_guard<std::mutex> lock(preferences_);
    if (restoreSerial_ != appliedSerial_) {
      if (s.selection.store(desired_)) {
        s.selectedId.store(0);
        appliedSerial_ = restoreSerial_;
        preferencesPending_.store(false);
      }
    } else
      s.selection.load(desired_);
  }
  void attempt() {
    try {
      auto next = std::make_unique<Mapping>(path_);
      auto &s = next->get();
      if (s.heartbeat.load() < uint64_t(nowUs() - 500000))
        return;
      if (!next->claimClient(audio_, token_))
        return;
      if (audio_ && connectedBefore_) {
        std::lock_guard<std::mutex> lock(preferences_);
        if (restoreSerial_ == appliedSerial_) {
          ++restoreSerial_;
          preferencesPending_.store(true);
        }
      }
      synchronize(s);
      connectedBefore_ = true;
      map_ = std::move(next);
      epoch_.fetch_add(1);
      current_.store(&map_->get());
    } catch (const std::exception &) {
    } // Missing/offline helper is an ordinary state.
  }
  void withdraw(bool final) {
    current_.store(nullptr);
    while (readers_.load())
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    if (!map_)
      return;
    auto &s = map_->get();
    if (audio_) {
      synchronize(s);
      s.audio.store(0);
      s.receive.store(0);
      if (final)
        s.selection.store({});
    } else {
      s.enable.store(0);
      s.startStop.store(0);
      s.syncRunHeartbeat.store(0);
    }
    auto &owner = audio_ ? s.audioOwner : s.syncOwner;
    auto expected = token_;
    owner.compare_exchange_strong(expected, 0);
    map_.reset();
  }

public:
  class Pin {
    Connector &owner_;

  public:
    Shared *ptr;
    uint64_t epoch;
    bool configured;
    explicit Pin(Connector &c) : owner_(c) {
      c.readers_.fetch_add(1);
      ptr = c.current_.load();
      epoch = c.epoch_.load();
      configured = !c.preferencesPending_.load();
    }
    Pin(const Pin &) = delete;
    ~Pin() { owner_.readers_.fetch_sub(1); }
  };
  Connector(bool audio, const char *path)
      : audio_(audio), path_(path && *path ? path : kDefaultSession),
        token_((uint64_t(getpid()) << 32) ^ uint64_t(nowUs()) ^
               reinterpret_cast<uintptr_t>(this)) {
    if (!token_)
      token_ = 1;
    attempt();
    worker_ = std::thread([this]() {
      while (!stop_.load()) {
        if (map_) {
          if (!map_->isCurrent(path_) ||
              map_->get().heartbeat.load() < uint64_t(nowUs() - 500000))
            withdraw(false);
          else
            synchronize(map_->get());
        } else
          attempt();
        for (unsigned i = 0; i < 10 && !stop_.load(); ++i)
          std::this_thread::sleep_for(std::chrono::milliseconds(10));
      }
      withdraw(true);
    });
  }
  ~Connector() {
    stop_.store(true);
    if (worker_.joinable())
      worker_.join();
  }
  Selection savedSelection() {
    Pin pin(*this);
    std::lock_guard<std::mutex> lock(preferences_);
    if (pin.ptr && restoreSerial_ == appliedSerial_)
      pin.ptr->selection.load(desired_);
    return desired_;
  }
  void restoreSelection(Selection value) {
    std::lock_guard<std::mutex> lock(preferences_);
    preferencesPending_.store(true);
    desired_ = std::move(value);
    ++restoreSerial_;
  }
};
} // namespace hector
