// SPDX-License-Identifier: GPL-2.0-or-later
#include "json.hpp"
#include "shared.hpp"
#include <fstream>
#include <iostream>
int main(int argc, char **argv) {
  using namespace hector;
  try {
    if (argc < 3)
      throw std::runtime_error("Use hector-link-control SESSION "
                               "status|channels|enable|audio|receive|tempo|"
                               "quantum|transport|start-stop|select [VALUE]");
    Mapping map(argv[1]);
    auto &s = map.get();
    const std::string command = argv[2];
    if (command == "channels") {
      const std::string path = std::string(argv[1]) + ".channels.json";
      int fd = open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
      struct stat st{};
      if (fd < 0)
        throw std::runtime_error("Channel snapshot unavailable");
      if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_uid != getuid() ||
          (st.st_mode & 0077) || st.st_size > 65536) {
        close(fd);
        throw std::runtime_error("Invalid channel snapshot");
      }
      std::string contents(size_t(st.st_size), '\0');
      const auto n = read(fd, contents.data(), contents.size());
      close(fd);
      if (n != ssize_t(contents.size()))
        throw std::runtime_error("Channel snapshot read failed");
      std::cout << contents;
      return 0;
    }
    if (command == "target") {
      if (argc != 5 || !s.selection.store({argv[3], argv[4]}))
        throw std::runtime_error(
            "Use target PEER STREAM (maximum 256 UTF-8 bytes each)");
      s.selectedId.store(0);
    } else if (command != "status") {
      if (argc != 4)
        throw std::runtime_error("Command needs a value");
      const std::string raw = argv[3];
      if (command == "select") {
        size_t end = 0;
        const auto v = std::stoull(raw, &end, 16);
        if (end != raw.size())
          throw std::runtime_error("Invalid ID");
        s.selectedId.store(v);
      } else {
        size_t end = 0;
        const double v = std::stod(raw, &end);
        if (end != raw.size() || !std::isfinite(v))
          throw std::runtime_error("Invalid number");
        if (command == "tempo" && v >= 20 && v <= 999)
          s.requestedTempo.store(bits(v));
        else if (command == "quantum" && v >= 1 && v <= 32)
          s.requestedQuantum.store(bits(v));
        else if (command == "transport" && (v == 0 || v == 1))
          s.requestedTransport.store(v == 1 ? 2 : 1);
        else if ((v == 0 || v == 1) &&
                 (command == "enable" || command == "audio" ||
                  command == "receive" || command == "start-stop")) {
          auto *field = command == "enable"    ? &s.enable
                        : command == "audio"   ? &s.audio
                        : command == "receive" ? &s.receive
                                               : &s.startStop;
          field->store(uint64_t(v));
        } else
          throw std::runtime_error("Unsupported command or value");
      }
    }
    Timeline t;
    if (!s.snapshot(t))
      throw std::runtime_error("Timeline busy; retry");
    const bool online = s.heartbeat.load() > uint64_t(nowUs() - 500000);
    const bool attached = map.clientAttached();
    const auto runHeartbeat = s.syncRunHeartbeat.load();
    const bool processing = online && attached &&
        runHeartbeat > uint64_t(nowUs() - 500000);
    Selection selection;
    if (!s.selection.load(selection))
      throw std::runtime_error("Selection busy; retry");
    std::cout << "{\"online\":" << (online ? "true" : "false")
              << ",\"interface\":" << quote(online ? std::string(s.interfaceName.data(), strnlen(s.interfaceName.data(),s.interfaceName.size())) : "")
              << ",\"sync_attached\":" << (attached ? "true" : "false")
              << ",\"sync_processing\":" << (processing ? "true" : "false")
              << ",\"enabled\":" << s.enable.load()
              << ",\"audio\":" << s.audio.load()
              << ",\"receive\":" << s.receive.load() << ",\"bpm\":" << t.bpm
              << ",\"beat\":" << beatAt(t, nowUs())
              << ",\"quantum\":" << t.quantum
              << ",\"playing\":" << (t.playing ? "true" : "false")
              << ",\"running\":"
              << (t.playing && nowUs() >= t.transportTime ? "true" : "false")
              << ",\"peers\":" << t.peers
              << ",\"selected_available\":" << s.selectedAvailable.load()
              << ",\"sent\":" << s.sent.load()
              << ",\"received\":" << s.received.load()
              << ",\"tx_overflow\":" << s.txOverflow.load()
              << ",\"rx_overflow\":" << s.rxOverflow.load()
              << ",\"missing_frames\":" << s.underrun.load()
              << ",\"target_peer\":" << quote(selection.peer)
              << ",\"target_stream\":" << quote(selection.stream) << "}\n";
    return 0;
  } catch (const std::exception &e) {
    std::cerr << e.what() << '\n';
    return 1;
  }
}
