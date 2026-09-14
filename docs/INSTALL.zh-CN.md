# 安装与运行

NAS Display 1.5.1，开发者 / 发布者 Dream。Debian 与 fnOS 两种安装方式二选一，网页端口均为 8787。没有连接屏幕也能实时监控。

## fnOS

在应用中心手动安装 NAS-Display-fnOS-1.5.1-x86.fpk。Python 3.12 依赖由应用中心处理；向导设置 8–128 字符的独立管理员密码，无默认密码。硬件准备在后台进行，网页显示进度；范围见 [驱动准备](DRIVERS.zh-CN.md)。

从 Debian 切换前执行 `sudo apt remove nas-display-host`，释放端口与 USB。旧 /var/lib/nas-display-host 配置保留，但密码与配置不会自动迁移；网页可导入旧 config.json，确认后保存。飞牛应用设置可重置密码、修改驱动策略，密码留空保持原值。

## Debian / Ubuntu

将当前 deb 复制到机器，在文件所在目录执行：

```bash
sudo apt install ./nas-display-host_1.5.1_all.deb
sudo nas-display-host setup
```

setup 仅首次安装执行，升级不重复初始化；APT 需要可用的软件源。已有配置和密码在升级时保留。密码重置使用 `sudo nas-display-host passwd`。

## 网页与屏幕

打开 `http://NAS的IP:8787` 登录，在“屏幕与采集”选择网卡、温度来源及 USB / Wi-Fi。初始发送暂停，确认屏幕配置后启用；不连接屏幕无需开启发送。

暂停屏幕发送只停止传输，网页采集继续。USB 与 Wi-Fi 配置、按键和烧录范围见 [屏幕说明](DISPLAY.zh-CN.md)。传感器的选择与缺失值处理见 [传感器说明](SENSORS.zh-CN.md)，调速见 [风扇说明](FANS.zh-CN.md)。

## 服务与排查

Debian 主服务 nas-display-host.service；fnOS 主服务 nas-display-fnos.service。风扇服务分别是 nas-display-fan.service 和 nas-display-fnos-fan.service。查看状态和日志使用 systemctl status、journalctl -u 对应服务名。关闭网页不停止调速；停止应用尝试恢复接管前主板模式，重启后恢复已启用曲线。

端口冲突先检查另一种安装方式是否仍运行。USB 不可用先确认数据线、串口权限和其他进程占用；固件更新前暂停发送。密码、采集、硬件准备或调速故障按网页具体错误和对应服务日志排查。

远程访问依赖已有的网络和飞牛访问通路，见 [远程访问](REMOTE-ACCESS.zh-CN.md)。NAS Terminal 是独立可选应用，安装与使用见 [终端说明](TERMINAL.zh-CN.md)。
