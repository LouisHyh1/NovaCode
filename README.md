# NovaCode

NovaCode 是一个使用 Python 实现的终端 AI 编程助手。项目支持 Windows 和 Linux，依赖与
Python 版本统一由 `uv.lock`、`.python-version` 和 `pyproject.toml` 管理。

## 环境要求

- Windows 10/11，推荐使用 PowerShell 7 和 Windows Terminal
- Linux，终端需支持 UTF-8
- `uv`

安装 `uv`：

```powershell
# Windows PowerShell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

```sh
# Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

`uv` 会按 `.python-version` 自动安装 Python 3.12，无需单独配置系统 Python。

## 安装与运行

同一份检出目录可能会被 Windows 和 WSL 同时访问。虚拟环境不能跨平台共用；Windows
访问 `\\wsl.localhost\...` 源码时，还应把虚拟环境放在本机 NTFS 目录，避免 UNC 小文件
读写显著拖慢安装和测试。

Windows PowerShell：

```powershell
$repo = (Get-Location).ProviderPath
$env:UV_PROJECT_ENVIRONMENT = "$env:LOCALAPPDATA\NovaCode\venv"
uv --directory $repo sync --locked
uv --directory $repo run nova
```

Linux / WSL：

```sh
export UV_PROJECT_ENVIRONMENT=.venv-linux
uv sync --locked
uv run nova
```

首次运行前，在项目的 `.novacode/config.yaml` 中配置 provider。该文件已被 Git 忽略：

```yaml
providers:
  - name: openai
    protocol: openai
    api_key: your-api-key
    model: your-model
```

## Shell 差异

工具协议中的名称仍为 `bash`，以保持现有会话和模型工具调用兼容；实际执行器会按平台选择：

- Windows：`cmd.exe /C` 语法，例如 `dir`、`set NAME=value`
- Linux：`/bin/sh -c` 语法，例如 `ls`、`export NAME=value`

模型会从环境提示中的 `Platform` 字段获知当前平台。项目路径请通过 `pathlib.Path` 或
`os.path` 处理，不要在 Python 代码中硬编码 `C:\...` 或 `/home/...`。

## 验证

```powershell
# Windows PowerShell
$repo = (Get-Location).ProviderPath
$env:UV_PROJECT_ENVIRONMENT = "$env:LOCALAPPDATA\NovaCode\venv"
$tmp = "$env:LOCALAPPDATA\NovaCode\pytest-tmp"
$env:TMP = $tmp
$env:TEMP = $tmp
$env:PYTEST_DEBUG_TEMPROOT = $tmp
New-Item -ItemType Directory -Force $tmp | Out-Null
uv --directory $repo run pytest -q
uv --directory $repo run ruff check src tests
```

```sh
# Linux / WSL
export UV_PROJECT_ENVIRONMENT=.venv-linux
uv run pytest -q
uv run ruff check src tests
```

GitHub Actions 会在 `windows-latest` 和 `ubuntu-latest` 上运行同一套测试，防止平台兼容性回归。
