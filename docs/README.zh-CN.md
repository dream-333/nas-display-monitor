# 文档导航

首次使用从安装开始；修改代码从开发部分开始。项目介绍见 [工程首页](../README.md)。

## 安装与使用

| 要做的事 | 文档 |
|---|---|
| 选择安装方式、升级、登录与管理服务 | [安装与运行](INSTALL.zh-CN.md) |
| 无终端安装（按飞牛 Compose 界面） | [图形界面部署](DOCKER-GUI.zh-CN.md) |
| 在铁牛 Debian NAS 上运行 Docker 镜像 | [Docker 部署](DOCKER.zh-CN.md) |
| 理解 CPU、GPU、硬盘、SYS 与风扇读数 | [传感器与温度来源](SENSORS.zh-CN.md) |
| 设置自动、手动百分比与温度曲线 | [风扇调速](FANS.zh-CN.md) |
| 查看驱动准备状态与主板支持边界 | [驱动准备](DRIVERS.zh-CN.md) |
| 连接、操作与升级 AMOLED 屏幕 | [屏幕使用](DISPLAY.zh-CN.md) |
| 全新屏幕首次完整烧录 | [首次烧录](FIRST-FLASH.zh-CN.md) |
| 安装和使用网页 SSH 终端 | [NAS Terminal](TERMINAL.zh-CN.md) |
| 排查 FN Connect 与 IPv6 访问 | [远程访问](REMOTE-ACCESS.zh-CN.md) |
| 打印与装配外壳 | [V7 外壳说明](../hardware/case/full-case-v7-slide/README.zh-CN.md) |

各组件常见问题保留在对应使用文档中，避免另维护一套重复步骤。

## 开发与交付

- [构建与验证](BUILD.zh-CN.md)：环境、测试、打包、固件和模型生成。
- [目录维护约定](REPOSITORY-LAYOUT.zh-CN.md)：源码、依赖、配置与生成物位置。
- [验证范围](VALIDATION.zh-CN.md)：已检查项目及需要实机验证的边界。
- [变更摘要](CHANGELOG.zh-CN.md)：功能版本和交付修订。

模块入口：[主机](../src/host/README.zh-CN.md) · [采集](../src/collector/README.zh-CN.md) · [终端](../src/terminal/README.zh-CN.md) · [固件](../src/firmware/README.zh-CN.md)。

## 文档维护

使用步骤以本目录的主题文档为准；源码 README 解释模块与开发入口。交付包根说明只负责安装顺序和导航，打包脚本直接带入同一份使用文档。`delivery/README-FIRST.zh-CN.md` 是组包模板，版本占位符由构建脚本替换，不是另一个用户入口。
