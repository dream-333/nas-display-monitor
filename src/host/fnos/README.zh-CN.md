# NAS Display fnOS 适配

- `lifecycle.py`：应用安装、升级、配置与服务管理。
- `hardware.py`：匹配芯片的后台驱动准备与状态记录。
- `fan_control.py`：本地风扇控制服务。
- `fpk_smart.py`：硬盘 SMART 读取服务。
- `vendor-lock.json`：固定构建依赖摘要。

用户操作见 [安装说明](../../../docs/INSTALL.zh-CN.md) 和 [驱动准备](../../../docs/DRIVERS.zh-CN.md)。构建入口为工程根目录的 `tools/build_host_fpk.py`，环境与完整验证见 [构建说明](../../../docs/BUILD.zh-CN.md)。
