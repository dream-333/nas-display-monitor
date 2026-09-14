# NAS Terminal 开发入口

安装、使用与故障排查统一见 [终端使用说明](../../docs/TERMINAL.zh-CN.md)。

## 模块与接口

- `server.py`：网页入口、网关身份校验、独立密码与 HTTP API。
- `terminal.py`、`pty_child.py`：本机 SSH、PTY 及终端会话管理。
- `lifecycle.py`：fnOS 安装、配置和服务生命周期。
- `static/`：网页、交互脚本及固定版本的 xterm 依赖。
- `tests/`：认证、会话与终端交互回归。

## 权限设计

- 网页和 SSH 客户端使用独立 `dream-terminal` 系统账户，拒绝以 root 运行；不授予 sudo 权限。
- 服务仅监听应用目录的 Unix socket，不开放 TCP 服务端口。
- 飞牛网关管理员身份校验 + 独立终端密码 + SSH 登录三层检查。Unix socket 允许本地网关连接，但伪造 Header 不能绕过终端密码和 SSH 认证。
- 终端密码仅保存 scrypt 派生值；会话令牌只保存在页面内存，绑定飞牛 UID。接口要求 JSON 与自定义 Header，不启用跨域访问。
- 不记录终端输入、SSH 密码或输出；SSH 密码只经过加密的浏览器连接及本机 SSH。系统 SSH 审计、shell 历史仍按 NAS 自己的设置运行。
- SSH 目标固定为本机，禁用代理转发、X11、端口转发、SSH 自动密钥和用户 SSH 配置。首次自动记录本机公钥；公钥变化时拒绝连接，不自动删除记录。
- 终端 HTML/JS/CSS 均本地加载；不启用外链识别、OSC 剪贴板权限或第三方统计。
- 停止应用时 systemd 清理网页和本地 SSH 客户端进程组；卸载删除本应用服务定义，不修改系统 SSH 或 NAS Display 服务。

## 构建与验证

在工程根目录运行；开发环境准备和完整交付检查见 [构建说明](../../docs/BUILD.zh-CN.md)。

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s src/terminal/tests -v
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/build_terminal_fpk.py
```

打包时从 `docs/TERMINAL.zh-CN.md` 取使用说明，不将本开发入口作为安装指南。

参考：


- [飞牛统一网关](https://developer.fnnas.com/docs/core-concepts/gateway-registration/)
- [飞牛应用入口](https://developer.fnnas.com/docs/core-concepts/app-entry/)
- [xterm.js 安全指南](https://xtermjs.org/docs/guides/security/)
