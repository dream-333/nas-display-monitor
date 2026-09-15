# NAS Display

Linux / fnOS 实时监控、风扇调速与 LILYGO T-Display-S3 AMOLED 状态屏。开发者 / 发布者：**Dream**。

已发布：NAS Display **1.5.1** · NAS Terminal **1.0.2** · AMOLED **UI5.1** · 外壳 **V7** · 交付修订 **r5**。

开发中：NAS Display **1.5.2** 移除 UDP 配对码；Wi-Fi 使用者须先升级免配对屏幕固件，再升级主机。现有 Release 的 UI5.1 固件仍使用配对码，详见 [屏幕升级说明](docs/DISPLAY.zh-CN.md)。

## 从这里开始

完整导航见 [文档中心](docs/README.zh-CN.md)。

- 安装：[交付文件](dist/README.zh-CN.md) · [安装说明](docs/INSTALL.zh-CN.md)。Debian 与 FPK 二选一，网页端口 8787，没有屏幕也能实时监控。
- Docker：[铁牛 NAS 容器部署](docs/DOCKER.zh-CN.md)，提供无终端的 [Compose 图形界面部署](docs/DOCKER-GUI.zh-CN.md)，自动初始化、持久配置、风扇控制和 SMART 采集。
- 使用：[传感器](docs/SENSORS.zh-CN.md) · [风扇曲线](docs/FANS.zh-CN.md) · [屏幕](docs/DISPLAY.zh-CN.md) · [终端](docs/TERMINAL.zh-CN.md)。
- 开发：[构建与验证](docs/BUILD.zh-CN.md) · [目录约定](docs/REPOSITORY-LAYOUT.zh-CN.md) · [验证范围](docs/VALIDATION.zh-CN.md)。
- 新屏首次烧录：[完整烧录说明](docs/FIRST-FLASH.zh-CN.md)。首次烧录清空配置；已有设备使用应用升级。
- 机械：[V7 模型与打印](hardware/case/full-case-v7-slide/README.zh-CN.md)。

## 功能

实时采集 CPU/GPU、内存、网络、硬盘温度、SYS 与风扇 RPM；网页与屏幕共享 CPU 温度选择。支持主板自动、手动百分比与温度曲线，提供静音、标准、性能、全速和自定义，最低输出允许 0%。已应用曲线随服务启动恢复，停止服务归还主板控制。

NAS Terminal 是独立应用，通过飞牛网关连接 NAS 本机 SSH；需要已有远程访问通路。已有 UI5.1 屏幕无需因文档修订重刷。

## 工程目录

```text
src/                 应用与设备源码
  host/              网页、调速与安装生命周期
  collector/         传感器采集
  terminal/          独立网页终端
  firmware/          ESP32-S3 AMOLED 固件
hardware/            外壳设计与硬件驱动
  case/              V7 源码、参考几何和发布模型
  drivers/it87/      固定驱动源码与维护工具
third_party/         第三方工具、官方参考和许可证
assets/              固定固件、图标和 PNG 预览
docs/               使用、构建与验证说明
tools/              构建、检查、打包脚本
tests/              回归测试与样本
dist/               可交付包与独立组件
```

本机运行配置、Python/PlatformIO 环境不进入源码包。第三方来源见 [许可说明](THIRD-PARTY-NOTICES.md)，修订内容见 [变更摘要](docs/CHANGELOG.zh-CN.md)。
