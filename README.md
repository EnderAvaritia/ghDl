# gh-dl: 批量 GitHub Release 下载工具

gh-dl 是一个命令行工具，用于从 GitHub Release 页面批量下载资源文件。支持基于 glob 模式的文件筛选、并发下载、智能缓存（跳过已下载的相同文件）、断点续传和三种操作模式（CLI、配置文件、交互式），让你无需手动点击就能批量获取发布包。

## 功能特性

- **三种操作模式**: 命令行直接下载、配置文件批量下载、交互式引导下载
- **并发下载**: 通过线程池同时下载多个资源，默认 4 个并发，可通过 `-j` 调整
- **Glob 模式匹配**: 使用 `fnmatch` 语法筛选文件名（如 `*.exe`、`*.zip`、`*-linux-*`）
- **智能缓存**: 已存在的文件若大小匹配则自动跳过，避免重复下载
- **断点续传**: 中断后重新运行会自动从断点继续（通过 `.part` 临时文件）
- **自动重试**: 下载失败时最多重试 3 次，间隔指数退避（1s, 3s, 9s）
- **文件命名安全**: Windows 不安全的字符（`\ / : * ? " < > |`）自动替换为下划线
- **优雅退出**: Ctrl+C 可安全中断下载，自动清理临时文件
- **版本选择**: 支持下载最新版（`latest`）或指定标签版本
- **扁平输出**: 可选择不创建 `owner/repo/version` 层级目录
- **模拟运行**: `--dry-run` 预览将要下载的文件，不实际写入
- **认证支持**: 通过 `GITHUB_TOKEN` 环境变量提升 API 限频配额

## 快速开始

### 安装

```bash
# 从项目目录安装
pip install -e .

# 验证安装
gh-dl --version
```

输出:

```
gh-dl 0.1.0
```

### 环境要求

- Python >= 3.10
- 依赖: `requests`（运行时）、`pytest` + `respx`（开发测试）

## 认证

GitHub API 对未认证的请求有严格的频率限制（每小时 60 次）。建议设置 `GITHUB_TOKEN` 环境变量以使用更高的配额（每小时 5000 次）。

```bash
# Linux / macOS
export GITHUB_TOKEN="ghp_your_token_here"

# Windows (PowerShell)
$env:GITHUB_TOKEN = "ghp_your_token_here"

# Windows (CMD)
set GITHUB_TOKEN=ghp_your_token_here
```

Token 可以从 [GitHub Settings > Developer settings > Personal access tokens](https://github.com/settings/tokens) 生成，不需要任何权限范围即可访问公开仓库的资源。

## 使用模式

### 1. CLI 模式：直接下载

```bash
# 下载某个仓库的最新 release 中的 .exe 文件
gh-dl download stedolan/jq --pattern "*.exe"

# 下载多个模式，指定版本和输出目录
gh-dl download microsoft/vscode --pattern "*.exe" --pattern "*.zip" --version "1.85.0" --output ./vscode

# 扁平输出（不创建层级目录）
gh-dl download stedolan/jq --pattern "*.exe" --flat

# 模拟运行，不实际下载
gh-dl download stedolan/jq --pattern "*.exe" --dry-run

# 调整并发数
gh-dl download stedolan/jq --pattern "*.exe" --concurrent 8

# 跳过缓存，强制重新下载
gh-dl download stedolan/jq --pattern "*.exe" --no-cache
```

### 2. 配置文件模式：批量下载

```bash
# 先生成示例配置文件
gh-dl init my-repos.json

# 编辑配置文件后执行批量下载
gh-dl config my-repos.json

# 覆盖输出目录
gh-dl config repos.json --output ./all-downloads

# 模拟运行
gh-dl config repos.json --dry-run
```

### 3. 交互式模式：引导式下载

```bash
# 不带参数运行，进入交互式模式
gh-dl
```

交互式模式下会逐一询问:

```
=======================================================================
  gh-dl Interactive Mode

  Add repositories one at a time. For each one you will be asked
  for the repo identifier, a file-name glob pattern, and an
  optional version/tag.  When you are done, confirm to start
  downloading.  Press Ctrl+C at any prompt to exit.
=======================================================================

Add a repo to download (Y/n)?
  Enter repo (owner/repo or URL): stedolan/jq
  File pattern (glob, e.g. *.exe): *.exe
  Version (Enter for latest, or tag like v1.0.0): latest
  -> [stedolan/jq] pattern='*.exe' version=latest

Add a repo to download (Y/n)?
  ...
```

## CLI 参考

### 全局选项

| 选项 | 说明 |
|------|------|
| `--version` | 显示版本号并退出 |
| `-h`, `--help` | 显示帮助信息 |

### download 子命令

从单个仓库下载资源。

```
gh-dl download <repo> --pattern <pattern> [options]
```

| 参数 | 说明 |
|------|------|
| `repo` | 仓库标识，格式 `owner/repo` 或完整 GitHub URL |
| `--pattern`, `-p` | Glob 匹配模式（可重复指定多个） |
| `--version`, `-v` | 版本标签，默认 `latest` |
| `--output`, `-o` | 输出目录，默认 `./downloads` |
| `--flat` | 扁平输出，不创建 `owner/repo/version` 层级 |
| `--dry-run` | 模拟运行，不实际写入文件 |
| `--concurrent`, `-j` | 并发下载数，默认 4 |
| `--no-cache` | 跳过缓存，强制重新下载 |

**示例:**

```bash
# 基本用法
gh-dl download neovim/neovim -p "*.zip" -p "*.gz"

# 通过 URL 指定仓库
gh-dl download https://github.com/stedolan/jq -p "*.exe"

# 指定具体版本
gh-dl download microsoft/vscode -p "*.exe" -v 1.85.0

# 自定义输出路径
gh-dl download neovim/neovim -p "*.appimage" -o ./nvim --flat
```

### config 子命令

根据配置文件批量下载多个仓库。

```
gh-dl config <config_file> [options]
```

| 参数 | 说明 |
|------|------|
| `config_file` | 配置文件路径（JSON 或 YAML） |
| `--output`, `-o` | 全局输出目录覆盖 |
| `--flat` | 全局扁平输出覆盖 |
| `--dry-run` | 模拟运行 |
| `--concurrent`, `-j` | 并发下载数，默认 4 |

**示例:**

```bash
gh-dl config repos.json
gh-dl config repos.yaml -o ./global-output --concurrent 8
```

### init 子命令

生成示例配置文件。

```
gh-dl init [output]
```

| 参数 | 说明 |
|------|------|
| `output` | 输出路径，默认 `gh-dl-config.json` |

**示例:**

```bash
gh-dl init
gh-dl init my-config.json
```

### list 子命令

列出仓库指定版本中的可下载资源。

```
gh-dl list <repo> [options]
```

| 参数 | 说明 |
|------|------|
| `repo` | 仓库标识，格式 `owner/repo` 或完整 URL |
| `--version`, `-v` | 版本标签，默认 `latest` |

**示例:**

```bash
gh-dl list stedolan/jq
```

输出:

```
Assets for stedolan/jq (jq-1.6):
  jq-win64.exe                                              1.00 MB
  → https://github.com/stedolan/jq/releases/download/jq-1.6/jq-win64.exe
  jq-linux64                                                1.95 MB
  → https://github.com/stedolan/jq/releases/download/jq-1.6/jq-linux64
  jq-osx-amd64                                              1.00 MB
  → https://github.com/stedolan/jq/releases/download/jq-1.6/jq-osx-amd64
```

## 配置文件格式

### JSON 格式

```json
{
  "repos": [
    {
      "owner": "stedolan",
      "repo": "jq",
      "pattern": "*.exe",
      "version": "latest",
      "output": "./downloads"
    },
    {
      "owner": "microsoft",
      "repo": "vscode",
      "pattern": "*.exe",
      "version": "1.85.0"
    }
  ]
}
```

### YAML 格式

需要安装 `pyyaml`:

```bash
pip install pyyaml
```

配置文件:

```yaml
repos:
  - owner: stedolan
    repo: jq
    pattern: "*.exe"
    version: latest
    output: ./downloads

  - owner: microsoft
    repo: vscode
    pattern: "*.exe"
    version: 1.85.0

  - owner: neovim
    repo: neovim
    pattern: "*.appimage"
    version: latest
```

### 配置字段说明

| 字段 | 必填 | 类型 | 默认值 | 说明 |
|------|------|------|--------|------|
| `owner` | 是 | string | - | GitHub 用户或组织名 |
| `repo` | 是 | string | - | 仓库名 |
| `pattern` | 是 | string | - | Glob 模式，如 `*.exe`、`*linux*` |
| `version` | 否 | string | `latest` | Release 标签，留空或 `latest` 取最新版 |
| `output` | 否 | string | 当前目录 | 下载输出路径 |

## 退出码

| 退出码 | 含义 |
|--------|------|
| `0` | 全部下载成功 |
| `1` | 部分下载失败（至少一个资源下载失败，但其他成功） |
| `2` | 严重错误（配置文件错误、API 错误、文件未找到、用户中断） |

## 目录结构

### 默认层级模式（`--flat` 未指定）

```
downloads/
  stedolan/
    jq/
      jq-1.6/
        jq-win64.exe
        jq-linux64
        jq-osx-amd64
  microsoft/
    vscode/
      1.85.0/
        VSCodeSetup-x64-1.85.0.exe
```

### 扁平模式（`--flat`）

```
downloads/
  jq-win64.exe
  jq-linux64
  jq-osx-amd64
```

## 使用技巧

### 提高 API 限频

```bash
# 设置 token 可以大幅提高 API 请求配额（60 -> 5000 次/小时）
export GITHUB_TOKEN="ghp_..."
```

### 下载多个模式

用多个 `-p` 参数组合不同的 glob 模式：

```bash
gh-dl download neovim/neovim -p "*.zip" -p "*.appimage" -p "*.gz"
```

### 批量下载后压缩

配合脚本自动打包下载结果：

```bash
gh-dl config repos.json --output ./archives
tar -czf releases.tar.gz ./archives
```

### 代理配置

gh-dl 使用 `requests` 库，支持标准环境变量代理：

```bash
# HTTP 代理
export HTTP_PROXY="http://127.0.0.1:7890"
export HTTPS_PROXY="http://127.0.0.1:7890"

# Windows PowerShell
$env:HTTP_PROXY = "http://127.0.0.1:7890"
$env:HTTPS_PROXY = "http://127.0.0.1:7890"

gh-dl download owner/repo --pattern "*.zip"
```

### 私有仓库

访问私有仓库需要设置有效的 `GITHUB_TOKEN`，且 Token 必须拥有 `repo` 权限范围。

## 常见问题

### Rate limit 限制

**问题**: 运行时出现 `GitHub API rate limit exceeded` 错误。

**原因**: 未认证的请求每小时仅限 60 次。

**解决**: 设置 `GITHUB_TOKEN` 环境变量，配额提升至每小时 5000 次。Token 从 GitHub Settings 生成，不需要特殊权限。

```bash
export GITHUB_TOKEN="ghp_your_token_here"
```

### Token 未生效

**问题**: 设置了 Token 但 API 错误仍然出现。

**解决**: 确认环境变量名称拼写正确（`GITHUB_TOKEN`，全大写），且没有空格。在命令行验证:

```bash
echo $GITHUB_TOKEN       # Linux/macOS
echo $env:GITHUB_TOKEN   # Windows PowerShell
```

### 没有找到匹配的文件

**问题**: 运行后显示 `No matching assets found`。

**原因**: Glob 模式与 Release 页面中的文件名不匹配。

**解决**: 先用 `gh-dl list` 查看实际的文件名:

```bash
gh-dl list owner/repo
```

根据输出调整 `--pattern` 参数。

### Unicode 文件名乱码

**问题**: 包含中文或其他 Unicode 字符的文件名显示异常。

**原因**: Windows 终端的默认编码可能不是 UTF-8。

**解决**: 设置 `PYTHONIOENCODING` 环境变量:

```bash
# Windows PowerShell
$env:PYTHONIOENCODING = "utf-8"

# 或在运行前执行
chcp 65001
```

### Windows 路径过长

**问题**: 下载嵌套层级较深时出现文件写入错误。

**原因**: Windows 经典路径长度限制（MAX_PATH = 260 字符）。

**解决**: 使用 `--flat` 参数跳过层级目录:

```bash
gh-dl download owner/repo --pattern "*" --flat
```

或者将输出目录设置在根目录下（如 `C:\downloads\`）以缩短路径。

### 下载被中断

**问题**: 下载过程中网络断开或误按 Ctrl+C。

**解决**: 重新运行相同的命令，gh-dl 会自动检测 `.part` 临时文件并断点续传。已完成的部分不会重复下载。

## 开发

### 安装开发依赖

```bash
pip install -e ".[dev]"
```

### 运行测试

项目包含 72 个单元测试，覆盖 API 客户端、下载管理器、配置文件解析、CLI 分派和工具函数:

```bash
pytest

# 带详细输出
pytest -v

# 指定测试文件
pytest tests/test_utils.py -v

# 指定测试函数
pytest tests/test_utils.py::test_safe_filename -v
```

### 项目结构

```
gh-downloader/
  gh_downloader/
    __init__.py      # 包版本信息
    __main__.py      # CLI 入口点（gh-dl 命令）
    api.py           # GitHub Releases API v3 客户端
    cli.py           # 参数解析与子命令分派
    config.py        # 配置文件加载与校验
    downloader.py    # 下载管理器（并发、重试、缓存）
    interactive.py   # 交互式模式
    utils.py         # 工具函数（路径、格式化、信号处理）
  tests/
    test_api.py       # API 客户端测试（12 个）
    test_cli.py       # CLI 测试（7 个）
    test_config.py    # 配置文件测试（19 个）
    test_downloader.py # 下载管理器测试（14 个）
    test_utils.py     # 工具函数测试（20 个）
  pyproject.toml
  README.md
```

## 许可

MIT License
