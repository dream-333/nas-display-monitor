# 当前交付文件

NAS Display 1.5.1 · NAS Terminal 1.0.2 · UI5.1 · V7 · 交付修订 r5，开发者 / 发布者 Dream。

| 文件 | 用途 |
|---|---|
| NAS-Display-1.5.1-r5-Delivery.zip | 安装与使用，包含主机、终端、屏幕升级工具、驱动和模型 |
| NAS-Display-1.5.1-r5-Source.zip | 继续开发，含源码、测试、固定构建输入与说明 |
| components/NAS-Display-AMOLED-UI5.1-First-Flash-r5.zip | 全新 AMOLED 首次烧录工具包，清空配置 |
| components/NAS-Display-AMOLED-UI5.1-factory.bin | 完整合并镜像，写入 0x0 |
| components/ | 单独下载或安装 FPK、deb、固件、驱动、模型及图标 |
| CONTENTS.json、SHA256SUMS.txt | 交付修订、组件角色、文件大小与摘要 |

Debian 与 NAS Display FPK 二选一，端口均为 8787。NAS Terminal 是可选独立应用。已有 UI5.1 屏幕无需因整理重刷。

r5 增加全新设备首次烧录包与完整合并镜像；应用版本和运行行为保持不变。安装包已重新构建，RELEASE.json 记录包装修订；它们的摘要与之前的同应用版本产物不同。无需仅为文档整理重新安装运行中的应用。

校验全部文件：在此目录运行 `sha256sum -c SHA256SUMS.txt`。单个组件的 .sha256 在 components/ 内校验。源码包不含用户配置、旧实验、日志或本机开发环境；安装 ZIP 不含开发源码全集。
