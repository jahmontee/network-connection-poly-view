// SPDX-License-Identifier: GPL-2.0-or-later
// Sync-only helper; no Link Audio streams, audio adapters or legacy sessions.
#include "shared.hpp"
#include "launch.hpp"
#include <ableton/Link.hpp>
#include <csignal>
#include <iostream>
#include <thread>
namespace {
volatile std::sig_atomic_t stop = 0;
void signalStop(int) { stop = 1; }
}
int main(int argc, char **argv) {
  using namespace hector;
  try {
    std::string path;
    std::string interface = std::getenv("HECTOR_LINK_INTERFACE") ? std::getenv("HECTOR_LINK_INTERFACE") : "";
    bool replace = false;
    for (int i=1;i<argc;++i) {
      const std::string arg=argv[i];
      if (arg=="--session" && i+1<argc) path=argv[++i];
      else if (arg=="--interface" && i+1<argc) interface=argv[++i];
      else if (arg=="--replace-stale") replace=true;
      else throw std::runtime_error("Use --session PATH [--replace-stale]");
    }
    if (interface.empty() || interface.size()>15 || interface.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")!=std::string::npos)
      throw std::runtime_error("An explicit network interface is required");
    setenv("HECTOR_LINK_INTERFACE",interface.c_str(),1);
    if (path.empty()) throw std::runtime_error("Session path required");
    if (replace && !Mapping::removeStale(path)) throw std::runtime_error("Existing helper/session active");
    Mapping map(path,true);auto &s=map.get();
    std::copy(interface.begin(),interface.end(),s.interfaceName.begin());
    ableton::Link link(120.);StartQuantizer quantizer;
    std::signal(SIGTERM,signalStop);std::signal(SIGINT,signalStop);
    std::cout << "{\"ready\":true,\"protocol\":4}" << std::endl;
    while(!stop) {
      const auto now=link.clock().micros();
      link.enable(s.enable.load()!=0);link.enableStartStopSync(s.startStop.load()!=0);
      const double quantum=std::clamp(cleanNumber(number(s.requestedQuantum.load()),4.),1.,32.);
      auto state=link.captureAppSessionState();bool changed=false;
      const auto request=s.requestedTempo.exchange(0);
      if(request && std::isfinite(number(request)) && number(request)>=20 && number(request)<=999) {
        state.setTempo(number(request),now);changed=true;
      }
      const auto transport=s.requestedTransport.exchange(0);
      if(transport) {
        const auto launch=transport==2 ? state.timeAtBeat(std::ceil(state.beatAtTime(now,quantum)/quantum)*quantum,quantum):now;
        state.setIsPlayingAndRequestBeatAtTime(transport==2,launch,0,quantum);changed=true;
      }
      if(changed) {link.commitAppSessionState(state);state=link.captureAppSessionState();}
      s.publish({now.count(),state.beatAtTime(now,quantum),state.tempo(),quantum,state.isPlaying(),link.numPeers(),quantizer.update(state,quantum)});
      s.heartbeat.store(now.count(),std::memory_order_release);
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    s.heartbeat.store(0);return 0;
  } catch(const std::exception &e) {std::cerr << e.what() << '\n';return 1;}
}
