// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <cstdint>
#include <iomanip>
#include <sstream>
#include <string>
namespace hector {
inline std::string quote(const std::string &s) {
  std::ostringstream out;
  out << '"';
  for (unsigned char c : s) {
    if (c == '"' || c == '\\')
      out << '\\' << c;
    else if (c < 32)
      out << "\\u" << std::hex << std::setw(4) << std::setfill('0')
          << unsigned(c);
    else
      out << c;
  }
  out << '"';
  return out.str();
}
inline std::string hexId(uint64_t v) {
  std::ostringstream out;
  out << std::hex << std::setw(16) << std::setfill('0') << v;
  return out.str();
}
} // namespace hector
