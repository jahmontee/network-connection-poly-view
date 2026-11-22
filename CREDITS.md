# Source, credits and licenses

**Hector adaptation, project direction and hardware testing** by jahmontee / Poly Hector. OpenAI Codex assisted with implementation, testing, documentation and packaging. This is an independent community project.

## Beebo and Hector touchscreen integration

Loki Davison, Poly Effects, and contributors wrote the upstream Beebo and Hector user interface. The release includes files adapted for Network Connection setup, the button and panel bindings, module discovery and picker ordering, and Poly / View. Modified copies and compatibility variants are identified in `payload-source-index.json`. Modifications were made in October and November 2026. The upstream project places files without a separate license notice under the GNU GPL. This distribution supplies the GPL version 3 text for those portions and retains any separate notices found in the files. [Poly Effects digit_ui](https://github.com/polyeffects/digit_ui/blob/feb4df451067850770ddd13af9f4e2828234dabc/README.md) · [Official firmware 4.0.7 source reference](https://www.polyeffects.com/firmware/improved-beebo-hector-407-installer)

## Bundled components

- **Ingen Python protocol support** by David Robillard and contributors. `ingen.py` retains its original permissive notice. [Ingen](https://drobilla.net/software/ingen/)
- **Link helper and control programs** include Ableton AG’s Link implementation and the Asio networking headers from Christopher M. Kohlhoff and contributors. Link retains GPL-2.0-or-later. Asio retains the Boost Software License and the included notices. `link-support/` provides the matching source and build commands for both shipped executables. The LV2 headers retain their own notices, and the GCC Runtime Library Exception accompanies the toolchain attribution. [Ableton Link](https://github.com/Ableton/link) · [Asio](https://github.com/chriskohlhoff/asio) · [LV2](https://lv2plug.in/)
- **Flash Repair** builds on Linux and the Poly Effects kernel tree. The two GPL-2.0 kernel modules have a separate corresponding-source archive containing the build configuration and changes. [Poly Effects Linux](https://github.com/polyeffects/linux)
- **AR9271 firmware** from Qualcomm Atheros and contributors is included unmodified. Its license and notices appear in the source archive and installed documentation. [open-ath9k-htc-firmware](https://github.com/qca/open-ath9k-htc-firmware)
- **Waitress** from the Pylons Project and contributors retains the Zope Public License and its notices. [Waitress](https://github.com/Pylons/waitress)
- **Inter** and **Press Start 2P** retain their SIL Open Font License notices. [Inter](https://github.com/rsms/inter) · [Press Start 2P](https://github.com/google/fonts/tree/main/ofl/pressstart2p)
- **Mala compatibility** includes editor source using DSP control conventions and display elements from Mutable Instruments Beads by Émilie Gillet. Existing notices and GPL terms remain with those files. The supported standalone Mala release has its own source and credits. [Mala release](https://github.com/jahmontee/mala-module/releases/tag/v0.3.0-beta.11) · [Mutable Instruments Eurorack source](https://github.com/pichenettes/eurorack)
- **Installer transaction foundation** retains the MIT notice from the community Mala installer. See `LICENSES/Installer-MIT.txt`.
