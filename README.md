# MemCleaner

高性能 Windows 内存优化工具，采用 Rust 内核 + Python CustomTkinter UI 架构。

## 功能特性

- **实时监控** — 物理内存占用、系统缓存、60 秒趋势图
- **一键清理** — 裁剪进程 Working Set，清理系统文件缓存与待机列表
- **进程管理** — 按内存占用排序，支持搜索、单进程清理
- **三种清理模式**
  - 保守 — 仅清理用户进程，最小化干扰
  - 均衡 — 日常推荐，兼顾效果与稳定性
  - 激进 — 最大释放效果，含系统工作集、Modified 列表、Standby 列表清理
- **自动清理** — 支持阈值触发（持续超过 N% 时自动清理）与定时触发
- **系统托盘** — 关闭窗口后后台运行；轻量模式退出 GUI，仅保留 Rust 守护进程
- **开机自启** — 写入注册表启动项，支持轻量 / 完整两种后台模式
- **管理员提权** — 可自动请求 UAC 提权以获得完整清理能力
- **深色 / 浅色主题**
- **中 / 英双语界面**
- **HiDPI 支持**

<img width="1354" height="977" alt="QQ_1778663054058" src="https://github.com/user-attachments/assets/4156d4f7-1e0e-41c1-81a8-18a20bc308df" />

## 架构

```
┌─────────────────────────────────────────────┐
│  Python UI (CustomTkinter)                  │
│  app.py · tray.py · monitor.py · scheduler  │
├─────────────────────────────────────────────┤
│  Rust Native Core (PyO3 → _core.pyd)        │
│  memory_stats · process_list · trim_all      │
│  clear_standby · enable_privilege            │
├─────────────────────────────────────────────┤
│  Rust Daemon (standalone binary)             │
│  memcleaner_daemon.exe — 轻量后台托盘+调度   │
└─────────────────────────────────────────────┘
```

| 层 | 语言 | 说明 |
|---|---|---|
| Rust 内核 | Rust + PyO3 | 编译为 `_core.pyd`，通过 Win32 API 调用 `EmptyWorkingSet`、`NtQuerySystemInformation`、`NtSetSystemInformation` 等完成内存操作 |
| Python UI | Python + CustomTkinter | 仪表盘、进程列表、设置页面，后台采样与调度线程 |
| Rust 守护进程 | Rust (standalone) | 轻量后台模式下替代 Python GUI，独立系统托盘 + 调度器 |

## 项目结构

```
src/
├── lib.rs                 # PyO3 模块：暴露给 Python 的所有函数
├── cleaning.rs            # 清理策略：保护名单、模式阈值、压力判断
└── bin/
    └── memcleaner_daemon.rs  # 独立 Rust 守护进程（轻量后台模式）

memcleaner/
├── __init__.py            # 版本号 + _core 导入
├── __main__.py            # python -m memcleaner 入口
├── app.py                 # 主窗口、布局、清理调度、托盘管理
├── config.py              # JSON 持久化配置 (%APPDATA%\memcleaner)
├── monitor.py             # 后台内存采样线程
├── scheduler.py           # 阈值/定时自动清理调度器
├── tray.py                # pystray 系统托盘（完整后台模式）
├── i18n.py                # 中/英文字符串表
├── theme.py               # 颜色、字体、图标常量
├── icon.py                # 应用图标绘制
├── autostart.py           # 注册表自启动管理
└── ui/
    ├── dashboard.py       # 仪表盘页面
    ├── processes.py       # 进程列表页面
    └── settings.py        # 设置页面

assets/
└── memcleaner.ico         # 应用图标
```

## 构建与运行

### 前置要求

- Python >= 3.10
- Rust toolchain (MSVC target)
- [maturin](https://www.maturin.rs/)

### 开发模式

```bash
# 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate

# 安装依赖
pip install maturin customtkinter Pillow pystray

# 编译 Rust 内核并安装到虚拟环境
maturin develop --release

# 运行
python -m memcleaner
```

### 打包单文件 EXE

```powershell
# 编译 Rust 内核 + 守护进程 + PyInstaller 打包
.\build_exe.ps1
```

产出：`dist\MemCleaner.exe`

## 快捷键

| 快捷键 | 功能 |
|---|---|
| `Ctrl+L` | 立即清理内存 |
| `Ctrl+R` | 刷新进程列表 |
| `Ctrl+1 / 2 / 3` | 切换到仪表盘 / 进程 / 设置 |
| `Esc` | 最小化到托盘 |
| `Ctrl+Q` | 退出程序 |
| `Ctrl+/` | 显示快捷键列表 |

## 配置

配置文件位于 `%APPDATA%\memcleaner\config.json`，所有设置均可在设置页面修改：

| 设置项 | 说明 |
|---|---|
| `cleaning_mode` | 清理模式：conservative / balanced / aggressive |
| `threshold_enabled` | 阈值自动清理开关 |
| `threshold_percent` | 触发阈值百分比 |
| `interval_enabled` | 定时清理开关 |
| `interval_minutes` | 定时间隔（分钟） |
| `clear_standby_too` | 清理时同时清除待机列表（需管理员） |
| `exclude_foreground_process` | 清理时排除前台应用 |
| `excluded_process_names` | 自定义排除进程列表（逗号分隔） |
| `auto_elevate` | 启动时自动请求管理员权限 |
| `autostart` | 开机自启 |
| `tray_enabled` | 系统托盘 |
| `background_mode` | 后台模式：light（Rust 守护进程）/ full（Python GUI） |
| `theme` | 主题：dark / light |
| `language` | 语言：zh / en |

## 技术细节

- 通过 `EmptyWorkingSet` 裁剪进程工作集，通过 `NtSetSystemInformation` 清理系统工作集、文件缓存、Modified 页列表和 Standby 列表
- 清理前后测量全局可用内存差值，报告真实释放量而非简单累加
- 关键系统进程（`csrss.exe`、`wininit.exe`、`dwm.exe` 等）和常用桌面程序（`explorer.exe`、`chrome.exe` 等）受保护，不会被清理
- Rust 守护进程通过命名互斥体保证单实例运行，支持 `--ensure`（启动或打开 GUI）和 `--quit` 命令

## 系统要求

- Windows 10 / 11（x64）
- 部分功能（清除待机列表等）需要管理员权限

## 许可证

Apache-2.0 license
