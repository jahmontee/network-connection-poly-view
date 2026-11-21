#!/bin/bash
set -eu
umask 077
exec /usr/bin/python3 -I -B /usb_flash/poly-view/install.py
