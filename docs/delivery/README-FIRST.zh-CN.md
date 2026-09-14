# NAS Display 交付包

NAS Display **@HOST_VERSION@** · NAS Terminal **@TERMINAL_VERSION@** · AMOLED **UI5.1** · 外壳 **V7**。开发者 / 发布者：**Dream**。交付标识：**@BUNDLE_NAME@**。

## 开始使用

1. 完整解压交付包。需要校验时，在解压目录执行 `sha256sum -c SHA256SUMS.txt`。
2. 根据系统选择 `fnOS/` 中的 FPK 或 `Debian/` 中的 deb，二选一。安装、首次设置和升级步骤统一见 `Documentation/INSTALL.zh-CN.md`。
3. 打开 `http://NAS的IP:8787`，选择网卡和温度源；不连接屏幕也能实时监控。
4. 按需要安装下表中的可选组件。已有 UI5.1 屏幕无需因本次文档修订重刷。

## 文件与使用说明

| 内容 | 文件位置 | 操作说明 |
|---|---|---|
| fnOS 主机应用 | `fnOS/` | `Documentation/INSTALL.zh-CN.md` |
| Debian / Ubuntu 主机应用 | `Debian/` | `Documentation/INSTALL.zh-CN.md` |
| 可选网页 SSH 终端 | `Terminal/` | `Documentation/TERMINAL.zh-CN.md` |
| 已有 AMOLED 屏幕的 Windows 升级工具 | `Windows-x64/` | `Documentation/DISPLAY.zh-CN.md` |
| V7 外壳模型与打印说明 ZIP | `Mechanical/` | 解压模型 ZIP 内的 README |
| 可选 IT8613 源码驱动工具包 | `Drivers/` | 驱动 ZIP 内的 README；自动准备见 `Documentation/DRIVERS.zh-CN.md` |
| 屏幕预览与许可 | `Preview/`、`Licenses/` | 对应目录 |

屏幕升级工具仅适用于已有本项目分区布局的 LILYGO T-Display-S3 AMOLED 非触摸版，不用于空白设备初始化。驱动工具包仅用于 IT8613；其他主板支持范围见驱动说明。

## 继续了解

- 温度与转速读数：`Documentation/SENSORS.zh-CN.md`。
- 手动百分比、曲线预设与重启恢复：`Documentation/FANS.zh-CN.md`。
- FN Connect 和 IPv6 排查：`Documentation/REMOTE-ACCESS.zh-CN.md`。
- 本次修订：`Documentation/CHANGELOG.zh-CN.md`；验证范围：`VERIFIED.zh-CN.md`。

`FILES.json`、`SHA256SUMS.txt` 和 `RELEASE.json` 记录文件摘要与版本。继续开发请使用单独的 `@BUNDLE_NAME@-Source.zip`，从其中 README 的开发入口开始。包内不含预置账号、用户配置、Wi-Fi 凭据或开发日志。
