# Third-party notices

This `flora/` folder is MIT-licensed (see `LICENSE`, same as the repository) **except** for the files listed below, which are copied from or derived from the TuyaOpen SDK and remain under the **Apache License, Version 2.0**, Copyright (c) Tuya Inc. A copy of the license is in `flora/firmware/LICENSE-APACHE-2.0`; Tuya's original license notice is in `flora/firmware/TUYAOPEN-LICENSE-NOTICE`.

Upstream: https://github.com/tuya/TuyaOpen at commit `2ea7925824db95dac5d42d7fa4e09ef8cc3c1e90`, example `examples/protocols/http_service_portal`.

| File in this repository | Upstream file | Status |
|---|---|---|
| `flora/firmware/pusheen_cat/web/assets/app.js` | `examples/protocols/http_service_portal/web/assets/app.js` | identical copy |
| `flora/firmware/pusheen_cat/tools/embed_web_assets.py` | `examples/protocols/http_service_portal/tools/embed_web_assets.py` | modified (2 lines added) |
| `flora/firmware/pusheen_cat/web/index.html` | `examples/protocols/http_service_portal/web/index.html` | modified (Korean setup form, QR flow) |
| `flora/firmware/pusheen_cat/src/portal.c` | `examples/protocols/http_service_portal/src/example_http_service_portal.c` | derived and substantially rewritten (setup hotspot, captive portal, provisioning handler) |
| `flora/firmware/pusheen_cat/CMakeLists.txt` | `examples/protocols/http_service_portal/CMakeLists.txt` | derived (web asset embedding step) |
| `flora/firmware/patches/0001-*.patch` | `src/libhttp/src/http_host.c` | patch to an Apache-2.0 file (adds `Cache-Control: no-store` and logging) |
| `flora/firmware/patches/0002-*.patch` | TuyaOpen-T5AI `t5_os/ap/components/lwip_intf_v2_1/lwip-2.1.2/src/core/tcp.c` (commit `69793c66bebf58ef4f886282626c2ea96a28f9a2`) | patch to a BSD-licensed lwIP file inside the Apache-2.0 platform tree (TCP close sends FIN) |

Changes to these files were made in October 2026 by the Flora authors. The rest of `flora/firmware/pusheen_cat/src/` (cat_*.c/h, cat_assets.c) was written for this project and is MIT.

Other third-party components are used at runtime but are not included in this repository:

- NVIDIA Nemotron 3.5 Lightning — OpenMDW-1.1 — accessed through Nebius Token Factory.
- TuyaOpen SDK and TuyaOpen-T5AI platform — Apache-2.0 — fetched at build time (pinned commits in `flora/firmware/README.md`).
- Device protocol compatible with xiaozhi-esp32 — MIT — https://github.com/78/xiaozhi-esp32 (no code copied).
- Fruit-fly circuit model: Shiu et al., *Nature* 2024, code MIT (philshiu/Drosophila_brain_model); FlyWire v630 data under FlyWire's terms — used by the CoFlorist dashboard repository.
- Opus codec (libopus, BSD-3-Clause) via the `opus` package on the server host.
