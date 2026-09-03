# CF 优选测速工具

根据本机当前网络，对 Cloudflare IP 节点做 TCP 延迟测试 + 下载测速，选出适合当前网络的最优节点。

## 目录结构

```
CF优选/
├── src/                  # 源码
│   ├── update.py         # CLI 核心引擎（兼容入口）
│   ├── models.py         # CLI/GUI 共享数据模型
│   ├── config.py         # CLI 参数校验与 JSON 默认配置
│   ├── input_sources.py  # 在线、本地、官方 IP 输入源
│   ├── latency.py        # TCPing/HTTPing 延迟测试
│   ├── speed_test.py     # curl/native 下载测速
│   ├── exporters.py      # 文本与订阅格式导出
│   ├── gui.py            # 旧版 GUI 兼容参考（纯函数/流程）
│   └── gui_redesign.py   # 新版 Signal Desk GUI，调用 update.exe
├── tests/                # 自动化测试（161 个）
├── packaging/            # 打包配置
│   ├── assets/icons/     # Signal Desk 图标资源
│   │   └── app_icon_v7.ico/png
│   ├── update.spec       # CLI 打包配置
│   ├── gui.spec          # GUI 打包配置（内嵌 update.exe）
│   └── update_desktop_shortcut.ps1 # 目录整理后更新桌面快捷方式
├── dist/                 # 打包产物（exe）
│   ├── CF优选测速工具.exe             # 主程序（快捷方式指向）
│   └── update.exe                     # CLI 核心（内嵌于主程序）
├── _cf_run/              # 运行工作目录（IP 列表/缓存/结果，自动生成）
├── pytest.ini            # pytest 配置
└── ITERATION_LOG.md      # 迭代日志
```

## 扫描策略

- `regional`：每个地区保留前 N 个候选，覆盖最广。
- `adaptive`：每个地区保留前 3 个，再加入全局延迟前 K 个；新版 GUI 默认使用，兼顾覆盖和效率。
- GUI 可对复测节点设置最大丢包率，超过阈值的节点不进入下载测速；CLI 默认阈值为 100%，保持兼容。

测速源支持预设：自动选择、Cloudflare、CM提供、移动专属，也可以切换到自定义并填写地址。第二阶段日志会显示最终实际使用的测速源。
- `global`：只保留全局延迟前 K 个，速度最快但地区覆盖最少。

快速采样模式会使用较小测速文件并提高并发；默认仍完整测试候选池，只有用户主动设置早停时才会在达到优选数量后停止。完整测速模式保持候选传入顺序。

无论使用哪种策略，输入列表中的节点都会先进入 TCP 阶段；桌面 GUI 每次都会重新测试 TCP，只有候选节点进入下载测速。CLI 可用 `--no-cache` 强制全量重测，默认仍保留缓存兼容。若初测可达率异常低，程序会对本轮失败节点进行一次低并发、较长超时的恢复补测，不读取历史结果。

### 代理使用建议

在线 IP 列表下载和节点测速使用独立代理：

```powershell
update.exe --input-proxy http://127.0.0.1:7890 --speed-proxy ""
```

其中 `--input-proxy` 只代理 CM 等在线列表下载，`--speed-proxy` 只代理节点 TCP/下载测速；测速代理留空表示直连，结果更接近本机真实网络。旧参数 `--proxy` 仍保留，表示两边都使用同一个代理，仅用于兼容旧命令。

桌面版高级设置中，“列表代理”对应 `--input-proxy`，“测速代理”对应 `--speed-proxy`。推荐只填写列表代理；如果测速代理留空，TCPing 和下载测速都不会经过列表代理。

## 使用

双击桌面快捷方式「CF优选测速工具」即可，程序文件名为 `CF优选测速工具.exe`。

- 数据源：在线列表（默认）/ 官方IPv4段 / 官方IPv6段 / 本地文件
- 测速完成后在「筛选候选」页可自定义筛选（地区/延迟/速度）并复制/导出所选节点
- 「筛选候选」支持一键全选当前筛选结果；选中状态仅显示在第一列，不改变整行颜色。

## 开发

推荐使用 Python 3.10+，依赖和可选开发工具统一声明在 `pyproject.toml`：

```powershell
python -m pip install -e ".[all]"
python -m pytest tests/ -q                  # pytest.ini 默认启用 -W error
```

打包（先打包 CLI，再打包内嵌 CLI 的 GUI）：

```powershell
python -m PyInstaller packaging/update.spec --distpath dist --workpath build/update
python -m PyInstaller packaging/gui.spec    --distpath dist --workpath build/gui
```

也可以直接调用 CLI 入口：

```powershell
cf-youxuan --help
```

更多构建约定见 `BUILDING.md`。

CLI 也支持：`--max-packet-loss 20`，表示过滤复测丢包率超过 20% 的候选节点。

## 致谢与许可证

本项目受 [CFData-WEB](https://github.com/PoemMisty/CFData-WEB) 和
[CF-ips-scanner](https://github.com/ethgan/CF-ips-scanner) 的功能思路启发，
代码为本项目独立实现；自动测速源 failover、官方 IP 段优选等思路参考自
CFData-WEB。

项目以 [GNU General Public License v3.0](./LICENSE) 发布。
















