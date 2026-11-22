# Network Connection / Poly / View v0.2.0-beta.4

This prerelease brings the Link Audio startup fix and the updated module picker to Hector firmware **4.0.7**. When Poly / View installs Link Audio Send or Receive, it now activates the module catalog before starting the audio services. Both services were verified on the first reboot after a browser batch install.

- The picker lists **Ableton Link**, **Ableton Link Audio Receive**, then **Ableton Link Audio Send** when all three are installed. Send shows its stereo inputs and Receive shows its left and right outputs on the canvas.
- The same signed ZIP supports a first install from USB and an update through Poly / View. It includes the SPI flash-transfer and JFFS2 preset-storage repairs.
- Ethernet, Wi-Fi, local browser access, and Remote Desktop are available from Network Connection and Poly / View.

## Install or update

For a first install, save your patch, extract the ZIP to the root of a FAT32 USB drive, and boot Hector with the drive inserted. A stock 4.0.7 card restarts twice. The patch canvas can appear between restarts. Allow **up to 5–6 minutes**, depending on SD card speed, and leave the drive inserted until Network Connection shows `v0.2.0-beta.4`.

To update a supported installation, sign into Poly / View and go to **Module Manager → Release packages → Upload ZIPs**. Choose the complete signed ZIP, confirm your patch is saved, and select **Update**. The browser displays progress and returns to sign-in after Hector restarts.

## Verification and recovery

This exact ZIP passed an offline USB install on a spare stock 4.0.7 SD card. Both storage repairs were active after the two restarts, and all 660 saved preset files and links matched the pre-install baseline. The beta.3-to-beta.4 update through Poly / View passed on Hector. Local checks also covered USB updates and failed-boot recovery.

An interrupted update can restore the previous working installation. For an explicit USB rollback of the matching installed beta.4 transaction, extract this release to a FAT32 USB drive and add `ROLLBACK-POLY-VIEW.txt` containing `network-connection-poly-view` on the first line and `0.2.0-beta.4` on the second. Save your patch and boot with the drive inserted. The shared storage repairs remain installed.

Matching source, license notices, and SHA-256 checksums are attached. [Source and credits](https://github.com/jahmontee/network-connection-poly-view/blob/v0.2.0-beta.4/CREDITS.md)
