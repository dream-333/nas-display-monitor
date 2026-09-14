# ESP32-S3 AMOLED 固件

面向 LILYGO T-Display-S3 AMOLED 非触摸版。硬件型号、页面操作、连接与升级见 [屏幕使用说明](../../docs/DISPLAY.zh-CN.md)。

## 代码入口

- `src/`：设备程序与绘制实现。
- `include/usb_protocol.h`：USB 协议；`include/protocol.h`：网络协议。
- `include/ui_state.h`：页面与设置状态。
- `platformio.ini`、`boards/`：工具链、依赖及板定义。
- `assets/`、`lib/`：固件资源与本地支持代码。

编译和预览命令统一见 [构建说明](../../docs/BUILD.zh-CN.md)。构建输出位于 `.pio/`，预览位于工程根目录 `.build/previews/`；两者不进入源码交付包。

固定发布输入为工程根目录 `assets/UI5.1/`，重新编译不会自动替换它。预览使用示例数据，不能代替实机屏幕验收。
