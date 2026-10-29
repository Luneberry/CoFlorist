# Flora cat firmware (Tuya T5AI board)

The cat runs on a Tuya **T5AI** board (BK7258, 8 MiB flash, 3.5" 320×480 ILI9488 display, microphone, speaker, one button). It is a TuyaOpen application: `pusheen_cat/`.

## What it does

1. On every power-on it opens a setup Wi-Fi hotspot and shows a QR code. Scan it with a phone, the captive portal opens, enter your home Wi-Fi. The server address is compiled in from `cat_profile_private.h` (the per-registration OTA URL from the DUO server).
2. It fetches the WebSocket address from the server's OTA endpoint, connects over WSS and sends `hello` with `features: {"listen_60s": true}`.
3. One button press opens a 60-second dialogue window: the on-device RNN VAD detects speech start/end, Opus (16 kHz, 60 ms) frames go to the server, the server answers with text (Nemotron) and speech (24 kHz Opus). The cat face shows the emotion; the speaker plays the answer; the microphone re-arms after playback.
4. Over USB (460800 baud) it prints `CAT_EVT` JSON lines (state, text, audio) for the local dashboard bridge.

Source entry points: `src/cat_network.c` (Wi-Fi, OTA, WebSocket, dialogue loop), `src/cat_voice.c` (codec/speaker), `src/cat_vad.c`, `src/cat_display.c` (320×480 frames, QR), `src/portal.c` (setup hotspot + captive portal), `src/cat_bridge.c` (USB events). `src/cat_assets.c` is generated from `../assets/frames-dark-v2` with `../tools/`.

## Build

Pinned SDK versions used for the submitted build:

| Repo | Commit |
|---|---|
| https://github.com/tuya/TuyaOpen | `2ea7925824db95dac5d42d7fa4e09ef8cc3c1e90` |
| https://github.com/tuya/TuyaOpen-T5AI (platform, fetched by `tos.py`) | `69793c66bebf58ef4f886282626c2ea96a28f9a2` |

```bash
git clone https://github.com/tuya/TuyaOpen.git && cd TuyaOpen && git checkout 2ea7925824db95dac5d42d7fa4e09ef8cc3c1e90
export TUYAOPEN=$PWD                   # SDK root, used below
source export.sh                      # sets up tos.py and the Python environment
cp -r /path/to/CoFlorist/flora/firmware/pusheen_cat apps/pusheen_cat
git apply /path/to/CoFlorist/flora/firmware/patches/0001-tuyaopen-http-host-cache-control-and-logging.patch
cd apps/pusheen_cat
cp src/cat_profile_private.example.h src/cat_profile_private.h   # then put your real OTA URL inside
tos.py build                           # app_default.config already selects T5AI / TUYA_T5AI_BOARD; the first build fetches the T5AI platform and toolchain
```

The T5AI platform is cloned by `tos.py` into `$TUYAOPEN/platform/T5AI` at the commit pinned in `platform/platform_config.yaml`. Apply the lwIP patch there (it makes TCP close send FIN instead of RST, which the server's TLS proxy needs), then rebuild from the app folder:

```bash
cd "$TUYAOPEN/platform/T5AI" && git apply /path/to/CoFlorist/flora/firmware/patches/0002-t5ai-lwip-tcp-close-fin.patch
cd "$TUYAOPEN/apps/pusheen_cat" && tos.py build
```

If `tos.py` asks for a platform choice interactively, pick `T5AI` → `TUYA_T5AI_BOARD` (the saved `app_default.config` carries `CONFIG_BOARD_CHOICE_T5AI=y`). These steps reproduce the environment the submitted binary was built with; a from-scratch rebuild on a fresh machine has not yet been re-run by the authors.

Output: `apps/pusheen_cat/.build/bin/pusheen_cat_QIO_*.bin`.

## Flash

Connect the board over USB (CH342 dual serial). Use Tuya's `tyutool` (installed with TuyaOpen) or `tos.py flash`. Write the QIO image at offset 0. Never write eFuses and never erase the whole NVS area.

## Notes

- `cat_profile_private.h` holds the only secret (your registration OTA URL). It is git-ignored; copy the example file.
- Wi-Fi credentials are not stored on the device; setup is repeated on each power-on by design.
- The display driver's VSYNC callback runs in interrupt context, so frame buffers are allocated once and reused (see `cat_display.c`).
