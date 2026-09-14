# IT8613E 驱动工具包

适用于已识别的 IT8613E，固定 frankcrawford/it87 提交 a904dd88b295a1bd4eeb47af523d8fad1e566a9f，模块版本 nasdisplay-a904dd88。GPL 源码与许可随包提供，必须针对目标机当前内核编译，不分发跨内核复用的 ko。

Centerm Zero1 pro 用户已反馈 DKMS 加载及转速采集可用。网页调速由 NAS Display 提供；本驱动包只负责驱动准备，不运行风扇测试或设置曲线。

## 安装

解压进入目录后执行：

```bash
sudo apt install build-essential dkms kmod python3
bash INSTALL.sh --check
sudo bash INSTALL.sh
```

需完整匹配当前内核的头文件、Module.symvers 和对应编译器。脚本验证源码与内核条件，通过 DKMS 安装，发现 IT8613 接口后配置开机加载。驱动加载会初始化硬件；不属于纯只读操作。已有不同驱动、签名或资源冲突时停止处理，不强制覆盖或绕过保护。

## 维护

FAN-CHECK.py 是只读诊断工具：运行 python3 FAN-CHECK.py，读取传感器、模式与温控参数。旧 FAN-PULSE.py 已撤回，不应运行先前复制的脉冲测试脚本。

IT8613 的手动输出与自动起始参数可能共享寄存器。恢复自动模式必须恢复接管前参数，仅写回模式编号并不完整。维护时按设备真实路径识别接口，不依赖 hwmonN 枚举编号；0 RPM 与温度、模式、报警结合判断。

卸载前停止依赖它的控制程序。只撤销本包创建的模块自动加载配置，再卸载模块及 it87/nasdisplay-a904dd88 的 DKMS 记录；不要移除其他版本或主板驱动。fnOS 应用卸载不会自动卸载此共享驱动。
