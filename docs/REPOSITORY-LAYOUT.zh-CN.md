# 目录维护约定

## 源码与输入

所有应用与设备源码统一位于 src/：host、collector、terminal、firmware。硬件设计与驱动位于 hardware/，第三方输入位于 third_party/。源码运行、测试和打包路径随目录更新，安装后的服务名称、数据目录和端口保持原约定。docs 按主题保存当前说明，组件 README 只提供必要入口，变更集中到 CHANGELOG。

third_party/tools、驱动固定源码、官方参考、许可证、assets 固件和机械 release 模型都是必要输入。许可副本随各独立组件保留。测试撤回入口只放 tests/fixtures，不放入驱动发布目录。

## 生成输出

检查日志、预览和候选安装包进入 .build；固件构建遵循 PlatformIO 使用 src/firmware/.pio。机械脚本输出 .build/mechanical/v7，不覆盖发布模型。HTML/SVG/NPZ 和原始日志不进入交付源码。

本机 .venv、.platformio 保留供继续开发，不打包。src/collector/config.json 是用户配置，保留且不导出。清理不创建历史垃圾备份，不删除测试所需样本。

## 交付

dist 顶层提供 Source 和 Delivery 两份 ZIP、说明、CONTENTS.json 与 SHA256SUMS.txt。独立 FPK、deb、驱动、模型、固件及图标在 components/，各自带摘要。

交付修订号在 tools/release_config.py，应用版本来自对应源码。仅文档与包装调整也必须更新交付修订标识及摘要；安装包中的 RELEASE.json 标明修订，不能把新包称作旧包的相同字节。软件行为变化则另行更新应用版本。

改动文件清单时核对旧摘要，再移除不再交付的条目；保留模型、驱动和固件的原始摘要，不能只刷新摘要掩盖内容变化。通过完整检查和解压重建后交付。

## 源码路径迁移

| 原路径 | 当前路径 |
|---|---|
| host-app/ | src/host/ |
| nas-probe/ | src/collector/ |
| terminal-app/ | src/terminal/ |
| firmware/ | src/firmware/ |
| mechanical/amoled-case/ | hardware/case/ |
| drivers/it87/ | hardware/drivers/it87/ |
| tools/vendor/ | third_party/tools/ |
| upstream/ | third_party/reference/ |
| licenses/ | third_party/licenses/ |
| release-assets/ | assets/ |

本机采集配置随源码迁移至 src/collector/config.json，内容和权限保留。若外部脚本使用旧源码绝对路径，请按表更新；正式安装包的系统路径没有迁移。不保留旧目录软链接，避免出现重复入口。
