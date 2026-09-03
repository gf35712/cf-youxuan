# CF 优选测速工具 — 迭代日志

## 2026-08-24 项目重建（原项目被 DSH 应用误删）

原项目（含源码/测试/打包产物）整个目录被删除。基于对代码结构的完整掌握 + 反编译原版 exe 作参考，从零重建。

### 重建内容
- src/update.py（61KB）：核心引擎 — TCP 延迟测试、下载测速（curl/native）、优选、结果/CSV/v2ray/clash 导出、TCP 缓存、多数据源合并、基准测试、二次验证（失败重试）、定时重跑、全参数校验
- src/gui.py（54KB）：customtkinter 界面 — 6 滑块、高级选项、主题切换、IP 定位（多源 failover）、线程安全 UI 调度、实时日志/进度/测速节点、结果展示
- packaging/：update.spec + gui.spec + 新图标（闪电光环）
- tests/：88 个测试（unit/integration/e2e/gui/gui_app）

### 检测结果（本轮）
- 静态分析：无未使用 import，`-W error` 严格模式干净
- 测试：**88 passed**（unit 32 + integration 10 + e2e 7 + gui 25 + gui_app 14）
- 真实 GUI 全生命周期：App + 真实 update.exe + 本地 HTTP 源 → ✅ 完成、3 优选、结果加载 PASS
- 真实端到端：3 节点全部优选（2.39-2.57 Mbps）

## 2026-08-25 借鉴 CFData-WEB 长处（适配 3 项）

### 新增功能
1. **自动测速源 failover**：speed_url 留空时按序探测 speed.cloudflare.com / speedtest.cloudflare.com，默认源不可达自动切换，避免单源抖动导致大面积误杀（借鉴 CFData-WEB 的 auto 源）。
2. **官方 IP 段数据源**：GUI 数据源下拉新增"官方IPv4段 / 官方IPv6段"，CLI 新增 --input-mode official / --official-iptype / --official-per-prefix，从 Cloudflare 官方段（ips-v4/ips-v6，下载失败回退内置段）每段随机采样候选 IP（轻量版全段扫描）。
3. **本地文件输入**：GUI 数据源下拉新增"本地文件"，浏览选择 txt/csv（等价 CLI -i，尊重本地文件绝不联网覆盖）。

### 未采纳（用户不需要 / 对测速无益）
- GitHub 上传导出（用户明确不需要）
- APK 安卓壳、HTTPing 扫描、ASN 数据中心分级（会引入自动区域筛选，违背用户诉求）
- 自定义 DNS（价值低）

### 验证
- 测试：101 passed（新增 5 个：官方段生成 v4/v6/上限钳制/CIDR 校验、自动测速源切换/全失败/空响应）
- 真实端到端：官方 IPv4 段 15 节点 → TCP 4 可达 → 测速 4 个全部完成；自动源选中 speed.cloudflare.com
- 旧配置兼容：source_type 默认 url，行为与之前完全一致
- 已重新打包 dist/update.exe + dist/CF优选测速工具.exe

## 2026-08-25 第二轮：HTTPing + 地区标注 + 待筛选列表

用户诉求：全加，但不要自动筛选 —— 给完整"待筛选列表"，用户自己勾选导出。

### 新增功能
1. **HTTPing 扫描模式**：CLI --scan-mode httping / GUI 高级选项"扫描方式"下拉。测 HTTP TTFB（含 TLS 握手+HTTP 处理），更贴近真实体感延迟；默认仍 TCPing。
2. **地区标注（仅标注不筛选）**：CLI --annotate / GUI 高级选项"地区标注"开关。对 region 为空的节点用 ip-api.com 批量查询 countryCode 标注（缓存到 annotate_cache.json），不参与任何筛选逻辑，只补充信息。
3. **筛选候选 tab（核心）**：GUI 新增"筛选候选"页 —— ttk.Treeview 表格列出全部结果（节点/地区/延迟/速度/优选），Ctrl/Shift 多选，支持：
   - 复制所选（剪贴板 ip:port#region 行）
   - 导出所选（格式取高级选项导出格式，默认 v2ray；复用 update 模块导出函数）

### 验证
- 测试：104 passed（新增 7 个：scan-mode 参数/标注 batch mock/全失败保留/GUI 命令传递/split_node）
- 真实端到端：官方 IPv4 段 + httping + annotate → 4 节点全部标注 US/CA，速度 15-24Mbps
- HTTPing 延迟比 TCPing 高属正常（TTFB 含 TLS，实测 664-734ms vs TCP 97ms）
- 已重新打包 dist/update.exe + dist/CF优选测速工具.exe

## 2026-08-25 全面验收（第二轮功能）

### 检查结果
- 测试：104 passed（`-W error` 严格模式）
- 静态扫描：无 eval/exec/pickle/yaml.load/shell=True；subprocess 全部 list 传参
- 恶意输入：file:// ftp:// 协议拒绝、非法端口/IP/空行拒绝、损坏配置回退默认、负参/越界参数 argparse 拒绝
- 边界：annotate 非 list/空响应/网络错误/坏 JSON 均不崩溃；httping 拒绝连接返回 None；官方段 per_prefix 钳制
- 修复：官方段模式跳过 TCP 缓存读写（随机 IP 永不命中且缓存文件无限膨胀）
- 真实 e2e（打包后 exe）：官方 IPv4 段 + httping + 标注 → 15 候选 → 5 可达 → 全部标注 CA/CR → 测速完成（最快 82.5Mbps）
- GUI 冒烟：启动 5s 存活不闪退（顶层 import update 成功，导出所选依赖打包正确）
- 快捷方式：指向最新 dist exe（31.4MB），有效
