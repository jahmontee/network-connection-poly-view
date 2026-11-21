# Building the bundled Link support binaries

Run `sh build-link.sh` with the Debian AArch64 GCC 12 cross toolchain installed.
The helper and controller use the vendored, pinned Asio, Ableton Link and LV2
sources in this directory. The original build used the source checkout at
`/tmp/work` and the output directory at `/tmp/output` in an isolated Debian
environment. This script uses paths relative to the source so it can run in a
normal checkout. See `SHA256SUMS` for the exact shipped binary digests.
