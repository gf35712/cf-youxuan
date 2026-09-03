# 构建说明

推荐使用 Python 3.10+，依赖和开发工具声明在 `pyproject.toml`。

```powershell
python -m pip install -e ".[all]"
python -m pytest tests -q
```

PyInstaller 构建时，CLI 必须先于 GUI；GUI spec 会嵌入 `dist/update.exe`。

```powershell
python -m PyInstaller packaging/update.spec --distpath dist --workpath .planning/cfyouxuan-stabilize/build-cli
python -m PyInstaller packaging/gui.spec    --distpath dist --workpath .planning/cfyouxuan-stabilize/build-gui
```

正式输出只保留在 `dist/`；构建中间文件不要写到项目根目录。

## 更新桌面快捷方式

目录或图标资源移动后，使用项目内脚本重新写入快捷方式，避免保留失效的旧图标路径：

```powershell
powershell -ExecutionPolicy Bypass -File packaging/update_desktop_shortcut.ps1
```

脚本会自动校验 `dist/CF优选测速工具.exe` 和 `packaging/assets/icons/app_icon_v7.ico`，存在桌面快捷方式时更新，不存在时创建。

## 构建环境建议

项目 spec 已排除 `numpy`、`matplotlib`、`pygame`、`pandas`、`scipy`、`PIL` 等非运行依赖，避免构建环境中的可选大包进入 exe。CLI/GUI 需要先后构建，GUI 会嵌入最新的 `dist/update.exe`。

