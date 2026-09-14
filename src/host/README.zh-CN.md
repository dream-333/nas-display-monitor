# NAS Display 主机服务

负责网页管理、采集调度、屏幕传输和风扇控制。安装与服务操作见 [安装说明](../../docs/INSTALL.zh-CN.md)。

## 代码入口

- `cli.py`、`app.py`：命令行与网页入口。
- `core.py`：运行配置、采集与发送调度；采集实现位于相邻的 `collector/`。
- `templates/`、`static/`：网页模板与静态资源。
- `usb_link.py`：屏幕 USB 握手、发送与确认。
- `fan_client.py`、`fan_curve.py`、`fan_hwmon.py`：控制服务通信、曲线与硬件接口。
- `debian/`、[fnos/](fnos/README.zh-CN.md)：安装及服务生命周期。

温度和调速语义分别见 [传感器](../../docs/SENSORS.zh-CN.md) 与 [风扇](../../docs/FANS.zh-CN.md)，修改实现时保持网页与屏幕一致。

开发环境、测试和构建命令统一见 [构建说明](../../docs/BUILD.zh-CN.md)。
