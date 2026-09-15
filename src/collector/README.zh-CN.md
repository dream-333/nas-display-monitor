# NAS 传感器采集模块

正式使用见 [安装说明](../../docs/INSTALL.zh-CN.md)，采集语义见 [传感器说明](../../docs/SENSORS.zh-CN.md)。已安装上位机时不要同时运行独立发送器。

collect.py、send.py、intel_pmu.py、board.py、storage.py 与 smart_cache.py 随当前上位机打包。

从本目录执行只读诊断：

```bash
python3 collect.py --diagnose
python3 collect.py --count 3 --interval 2
```

首次独立配置可运行 `python3 send.py --init`，会自动发现网卡和硬盘、GPU 使用 auto，并创建私有 config.json；已有文件拒绝覆盖。按 [屏幕说明](../../docs/DISPLAY.zh-CN.md) 配置屏幕 Wi-Fi 地址和 UDP 端口（无需配对码；屏幕需升级免配对固件），然后运行 `python3 send.py`。USB 发送请使用 Debian 网页上位机。

通用 CPU 温度为 cpu_temp_c，GPU 温度为 temperature_c；原 cpu_tctl_c / edge_c 仅保留对应 AMD 传感器语义。没有独立 GPU 温度时不复用 CPU 温度，不可用指标输出 null。GPU 占用率优先使用 gpu_busy_percent；Intel i915 还支持原生 PMU 采集，取最忙引擎的百分比。

Intel i915 PMU 采集需要一起保留 intel_pmu.py；受限内核需 CAP_PERFMON（deb 服务自动配置）。独立诊断用 sudo python3 collect.py --diagnose。

风扇：自动读取 fanN_input 的 RPM，分别列出 PWM 与目标转速接口；`python3 collect.py --diagnose-fans` 可输出诊断。只读，不执行调速。
