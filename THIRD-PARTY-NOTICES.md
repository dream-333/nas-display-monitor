# 第三方组件与来源

项目开发者 / 发布者为 Dream。工程交付不改变任何已有组件的许可证；不能将整个目录简单视为统一 MIT 许可。本项目原创部分尚未声明统一的开源许可证，公开再发布时应由权利人明确授权范围。

| 组件 | 来源、版本与许可位置 |
|---|---|
| LILYGO AMOLED 板级驱动 / 官方资料 | `src/firmware/lib/amoled/LICENSE`、`third_party/reference/amoled/`、`hardware/case/official/LICENSE` |
| Arduino-ESP32 / ESP-IDF 引导与 OTA 初始化 | `assets/UI5.1/BOOT-SOURCES.json` 记录 Arduino-ESP32 2.0.14 / ESP-IDF 4.4.6 来源；`assets/UI5.1/Licenses/Arduino-ESP32.txt`、`ESP-IDF.txt` 保留许可 |
| TFT_eSPI、ArduinoJson | `src/firmware/platformio.ini` 锁版本，`third_party/licenses/` 与 `assets/UI5.1/Licenses/` 保留许可 |
| 屏幕字体 | `src/firmware/assets/fonts/LICENSE.txt`、`assets/UI5.1/Licenses/Dream-UI-Sans.txt` |
| it87 扩展驱动 | `hardware/drivers/it87/SOURCE.json` 固定提交；`source/COPYING`、`source/SHA256SUMS`，保留对应 GPL 源码 |
| SMART 工具 | `third_party/tools/fnos/` 保留 smartmontools 7.3 Debian 二进制与对应源码、补丁和 dsc；FPK 打包时附源码和许可 |
| Python FPK 运行依赖 | `src/host/fnos/vendor-lock.json` 锁定 wheel 摘要，wheel / 包内 dist-info 含许可 |
| fnpack | `third_party/tools/fnos/fnpack`，官方工具；摘要位于上述 vendor-lock，来源说明见 `src/host/fnos/README.zh-CN.md` |
| Windows esptool 4.12.0 | `third_party/tools/esptool/` 保存原始发布 ZIP，`tools/package_release.py` 锁定摘要；解包许可随 Windows 工具交付 |
| xterm.js / addon-fit | `src/terminal/vendor-lock.json` 记录版本、来源和摘要；`src/terminal/static/vendor/` 保留 MIT 许可 |

`requirements-dev.txt` 与 `requirements-design.txt` 只定义开发依赖，不表示这些包全部进入应用。源代码 ZIP 中保留的 vendor 工具用于复现构建，不包含开发机的完整 Python / PlatformIO 环境。
