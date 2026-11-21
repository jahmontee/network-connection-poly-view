// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <cstdlib>
#include <cstring>
#include <stdexcept>
#include <sys/socket.h>
namespace hector {
inline void bindSelectedSocket(int fd) {
  const char *name=std::getenv("HECTOR_LINK_INTERFACE");
  if(!name || !*name || std::strlen(name)>15)
    throw std::runtime_error("A selected network interface is required");
  // Binding only the source IP is insufficient when both NICs share a subnet.
  // Pin unicast as well as multicast to the selected device; never fall back.
  if(::setsockopt(fd,SOL_SOCKET,SO_BINDTODEVICE,name,std::strlen(name)+1))
    throw std::runtime_error("Cannot bind Link socket to selected interface");
}
}
