#!/bin/sh
set -eu
cd "$(dirname "$0")"
mkdir -p build
"${CXX:-aarch64-linux-gnu-g++-12}" -std=c++17 -O2 -fno-fast-math -Wall -Wextra -Werror -pthread -I./src -I./vendor/lv2-headers -I./build -I./sdk-overlay -isystem ./vendor/link/include -isystem ./vendor/asio/include -DLINK_PLATFORM_LINUX=1 -DLINK_PLATFORM_UNIX=1 -DASIO_STANDALONE=1 -DASIO_NO_TYPEID=1 -DASIO_VERSION_NAMESPACE=hector_link_asio -Wno-multichar -Wno-unused-parameter ./src/helper.cpp -latomic -o ./build/helper
"${CXX:-aarch64-linux-gnu-g++-12}" -std=c++17 -O2 -fno-fast-math -Wall -Wextra -Werror -pthread -I./src -I./vendor/lv2-headers -I./build ./src/control.cpp -latomic -o ./build/control
