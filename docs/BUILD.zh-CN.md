# 构建、验证与交付

在工程根目录运行。参考构建环境为 **Linux x86_64、Python 3.12**；FPK 的原生依赖按这个组合打包。已安装的 `.venv` 与 `.platformio` 可继续使用。

## 新机器准备

以下系统包命令适用于 Ubuntu 24.04；其他系统需准备同等工具，尤其 Python 3.12、dpkg、systemd-analyze、Cairo 和 C++ 编译器。

```bash
sudo apt install python3.12 python3.12-venv build-essential dpkg-dev libcairo2 libffi-dev pkg-config
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m playwright install --with-deps chromium
chmod +x third_party/tools/fnos/fnpack
```

浏览器检查优先使用 `NAS_DISPLAY_BROWSER` 指定路径，其次系统 Google Chrome，否则使用 Playwright 的 Chromium。ZIP 解压工具可能不保留执行权限，因此显式恢复 fnpack 的执行位。

`third_party/tools/` 保留带摘要锁定的 fnpack、Python wheels、SMART 包与对应源码、Windows esptool；它们是构建输入。Python 开发依赖和初次 PlatformIO 工具链安装仍需网络，工程包不是离线工具链镜像。版本和组件来源见 [第三方说明](../THIRD-PARTY-NOTICES.md)。

## 检查整个工程

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/check_release.py
```

在临时目录运行主机与终端测试，构建并验证 Debian / FPK、独立终端、驱动和 V7 外壳 ZIP，最后组装安装交付包及工程源码包。不会覆盖 `dist`，不会安装服务、加载驱动、写风扇或烧录屏幕。结果在 `.build/logs/`。

单独运行测试：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s src/terminal/tests -v
```

## 生成交付候选

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/check_release.py --export .build/candidate
```

目标目录必须不存在。只有全部检查成功后才导出：顶层是两份 ZIP 与清单，components/ 是独立组件。NAS_DISPLAY_OUTPUT_DIR 指定的是交付根目录，组件工具自动使用其中的 components/。

验证源码可交付性：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/package_source.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/verify_source.py --export .build/from-source
```

解压源码后执行完整检查，可将重建结果导出到新的候选目录。依赖已安装的 Python 和系统工具，不依赖原工程配置或历史缓存。交付时使用验证通过的候选目录，替换 dist 中旧产物后核对清单。

package_release.py 要求当前版本检查日志；check_release.py 会重新生成，不能复用源码修改前的通过记录。文件、包内容和文档链接由检查工具验证。版本与交付修订由 tools/release_config.py 统一读取。

## Docker 镜像

针对当前铁牛 Debian x86_64 NAS，在根目录运行 `docker build -t nas-display:1.5.2 .`；多阶段构建使用固定基础镜像、仓库内校验摘要的应用依赖，以及 Debian 签名快照源中的 6.1.148-1 头文件和 GCC 12；编译 IT8613 模块后只将驱动及对应源码带入运行镜像。首次构建需联网访问快照源，运行时无需安装依赖。构建后通过 `tools/package_docker.py` 导出镜像导入 ZIP；无终端安装使用 `tools/package_docker_gui.py` 导出 Compose 项目 ZIP，内含完整运行环境与 FROM scratch 本地组装文件，不需要在 NAS 拉取基础镜像。部署及验证见 [Docker 说明](DOCKER.zh-CN.md)。运行配置使用 Docker 数据卷，镜像不包含本机账户和 Wi-Fi 配置。

## 固件与机械设计

编译只生成本地输出，不自动烧录：

```bash
PLATFORMIO_CORE_DIR="$PWD/.platformio" .venv/bin/pio run -d src/firmware
.venv/bin/python tools/render_ui_preview.py
```

PlatformIO 中的框架和库已锁版本；首次会下载工具链。发布使用 `assets/UI5.1/` 固定应用和引导输入；`tools/package_firmware.py` 离线校验 ESP 镜像、分区和各段地址，生成 First-Flash ZIP 与 factory.bin。引导来源见该目录 BOOT-SOURCES.json；首次烧录真实启动尚未验收。发布保留既有 UI5.1 应用字节，重新编译的固件不自动替换发布输入。预览在 `.build/previews/`，构建在 `src/firmware/.pio/`。

需要重做字模、机械模型或预览时再安装：

```bash
.venv/bin/python -m pip install -r requirements-design.txt
```

V7 建模另需系统 OpenSCAD；其目录下 `build-and-check.py` 将 STL、检查结果和日志生成到 `.build/mechanical/v7/`；`render-preview.py` 使用同一输出目录。固定模型只需打包时不依赖 OpenSCAD。硬件限制见 [屏幕说明](DISPLAY.zh-CN.md) 和 [机械说明](../hardware/case/full-case-v7-slide/README.zh-CN.md)。

无桌面的 Linux 环境运行机械 PNG 预览时，需要系统 Xvfb，并使用：

```bash
xvfb-run -a .venv/bin/python hardware/case/full-case-v7-slide/render-preview.py
```

先运行同目录的 build-and-check.py 生成预览数据。只有机械渲染需要此步骤；主机安装包构建不依赖 VTK 或显示服务。
