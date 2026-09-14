# 全新 AMOLED 屏幕首次烧录

适用 **LILYGO T-Display-S3 AMOLED 非触摸版，1.91 英寸、RM67162、ESP32-S3、16 MB Flash**。不是普通 ESP32-S3 开发板、LCD 或触摸屏的通用固件。

## 选择首次烧录还是升级

| 设备状态 | 操作 | 配置 |
|---|---|---|
| 新板、出厂演示固件、其他固件或需要恢复出厂状态 | 本包 `FIRST-FLASH.cmd` / `first_flash.py` | 清空整片 Flash，包括 Wi-Fi 和屏幕设置 |
| 已运行本项目并使用相同分区布局 | 完整交付包的 `Windows-x64/UPDATE.cmd` | 仅更新应用，保留配置 |

首次烧录包包含引导程序、分区表、OTA 初始化数据及 UI5.1 应用，已经合成为 `factory.bin`。不需要先刷其他版本。已有 UI5.1 正常运行的设备不需要重刷。

## Windows：无需安装 Python

1. 完整解压首次烧录 ZIP；使用完整交付包时进入 `First-Flash/`。不要在压缩包内直接运行脚本。
2. 使用 USB-C **数据线**连接屏幕，暂停 NAS Display 的 USB 发送并关闭串口监视器。
3. 双击 `FIRST-FLASH.cmd`，输入设备 COM 口，例如 `COM5`。
4. 确认板型，输入 **ERASE** 同意清空设备。脚本先校验文件，再检查芯片与 16 MB Flash，随后执行完整擦写。检测失败不执行擦写。
5. 出现 SUCCESS 后松开 BOOT，按 RESET；如设备仍停留在下载模式，拔插 USB 重启。

找不到串口或连接失败时：按住 BOOT，短按 RESET，再松开 BOOT，重新查看设备管理器中的 COM 口并运行脚本。进入下载模式后串口号可能变化。

只检查文件和显示命令，不连接设备：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\first-flash.ps1 -Port COM5 -DryRun
```

## Linux / macOS

需要 Python 3.9 或更新版本。在首次烧录包目录准备 esptool 环境：

```bash
python3 -m venv .flash-venv
.flash-venv/bin/python -m pip install esptool==4.5.1
.flash-venv/bin/python first_flash.py --port /dev/ttyACM0
```

macOS 将端口换为实际 `/dev/cu.usbmodem...`。按提示输入 ERASE；若提示串口权限不足，先配置系统串口访问权限。安装 Python 依赖需要网络；Windows 工具已随包提供。

加入 `--dry-run` 只校验文件和显示命令，不需要安装 esptool，也不会接触设备。脚本输出失败时不要视为烧录成功。

## 烧录后连接 NAS

屏幕启动后，在 NAS Display 网页的“屏幕与采集”选择 USB 直连，选择重新枚举的串口并启用发送。USB 不需要 Wi-Fi 配对；收到主机数据前出现等待或配网提示是正常情况。确认网页显示屏幕已连接、发送计数增加且屏幕指标更新。

首次擦写会恢复配色、亮度、休眠和 Wi-Fi 设置。Wi-Fi 模式需要重新配网；USB 模式可直接使用。

## 手工烧录与文件说明

`factory.bin` 写入 **0x0**；应用镜像 `firmware.bin` 单独升级时写入 **0x10000**，两者不能混用。手工执行首次烧录前确认设备和清空意图：

```bash
python -m esptool --chip esp32s3 --port /dev/ttyACM0 --baud 460800 write_flash --flash_mode keep --flash_freq keep --flash_size keep --erase-all 0x0 factory.bin
```

以上 Python 必须来自已安装 esptool 的环境。脚本方式还会先检查 Flash 容量，推荐使用脚本。

| 内含镜像 | 原始地址 |
|---|---|
| `bootloader.bin` | `0x0` |
| `partitions.bin` | `0x8000` |
| `boot_app0.bin`（OTA 初始化） | `0xe000` |
| `firmware.bin` | `0x10000` |

`FLASH.json` 记录板型、地址、摘要与清空行为，`BOOT-SOURCES.json` 记录来源，`FILES.json` / `SHA256SUMS.txt` 用于文件校验。固定引导程序来自相同锁定工具链，应用固件沿用已发布 UI5.1 字节；合并镜像保留空白区域，完整擦除由烧录命令执行。无需额外烧录文件系统镜像。

本包经过离线镜像、分区、合并内容及脚本模拟验证；首次全擦写启动和 Windows 实际串口操作仍需对应实机验收。本次组包未连接或擦写任何设备。
