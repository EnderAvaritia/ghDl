# gh-dl: 批量 GitHub Release 下载工具

gh-dl 是一个命令行工具，用于从 GitHub Release 页面批量下载资源文件。它支持 glob 或正则表达式文件筛选、并发下载、智能缓存（跳过已下载的相同文件）、断点续传和三种操作模式。你无需手动点击页面，一条命令即可批量获取任意仓库的发布包。

- [用法一览](#用法一览)
- [功能特性](#功能特性)
- [安装](#安装)
- [快速上手](#快速上手)
- [使用模式](#使用模式)
- [配置文件格式](#配置文件格式)
- [CLI 参考](#cli-参考)
- [退出码](#退出码)
- [技巧与常见问题](#技巧与常见问题)
- [开发](#开发)
- [许可](#许可)

## 用法一览

```
$ gh-dl list stedolan/jq

Assets for stedolan/jq (jq-1.8.1):
  jq-win64.exe                                              1.00 MB
  -> https://github.com/stedolan/jq/releases/download/jq-1.8.1/jq-win64.exe
  jq-linux64                                                1.95 MB
  -> https://github.com/stedolan/jq/releases/download/jq-1.8.1/jq-linux64
  jq-osx-amd64                                              1.00 MB
  -> https://github.com/stedolan/jq/releases/download/jq-1.8.1/jq-osx-amd64
```

```
$ gh-dl download stedolan/jq -p "*linux*" -p "*.exe"

  jq-linux64: [####################] 100% 1.95 MB/1.95 MB 2.3 MB/s
  jq-linux-arm: [####################] 100% 1.71 MB/1.71 MB 1.9 MB/s
  jq-win64.exe: [####################] 100% 1.00 MB/1.00 MB 1.5 MB/s
Done: 3 downloaded, 0 cached, 0 errors
```

## 功能特性

- 三种操作模式：CLI 直接下载、配置文件批量下载、交互式引导下载
- 并发下载：线程池同时下载，默认 4 个并发，通过 `-j` 调整
- 灵活的模式匹配：支持 Glob（`fnmatch`，如 `*.exe`）和正则表达式（`--regex`，如 `.*\.exe$`）两种筛选方式
- 实时进度条：下载时显示 `[####------------]` 进度条、百分比、速度
- 智能缓存：已存在的文件若大小一致则自动跳过
- 断点续传：中断后重新运行，从 `.part` 临时文件继续下载
- 自动重试：下载失败最多重试 3 次，指数退避间隔（1s、3s、9s）
- 文件名安全：Windows 不合法字符（`\ / : * ? " < > |`）自动替换为下划线
- 优雅退出：Ctrl+C 安全中断，自动清理临时文件
- 版本选择：支持最新版（`latest`）或指定标签版本
- 扁平输出：可选择不创建 `owner/repo/version` 层级目录
- 模拟运行：`--dry-run` 预览将下载的文件，不实际写入
- 认证支持：通过 `GITHUB_TOKEN` 环境变量或 `.gh-dl.json` 配置文件提升 API 限频配额
- 持久配置：Token 和代理设置写入文件，无需每次终端重复设置

## 安装

### 环境要求

- Python >= 3.10
- 运行时依赖：`requests`

### 从源码安装

```bash
pip install -e .
gh-dl --version    # 输出: gh-dl 0.1.0
```

## 快速上手

```bash
# 列出资源确认文件名
gh-dl list neovim/neovim

# 下载所有 .zip 文件，保存到 ./nvim
gh-dl download neovim/neovim -p "*.zip" -o ./nvim

# 模拟运行，不实际写入
gh-dl download neovim/neovim -p "*.zip" --dry-run
```

## 使用模式

### 1. CLI 模式：直接下载

```bash
# 下载最新 Release 中的 .exe 文件
gh-dl download stedolan/jq -p "*.exe"

# 多个文件类型 + 指定版本 + 自定义输出
gh-dl download microsoft/vscode -p "*.exe" -p "*.zip" -v 1.85.0 -o ./vscode

# 直接使用 Release URL（自动提取版本号）
gh-dl download https://github.com/jeessy2/ddns-go/releases/tag/v6.17.0 -p "*.zip"

# 扁平输出，不创建 owner/repo/version 层级
gh-dl download stedolan/jq -p "*.exe" --flat

# 跳过缓存，强制重新下载
gh-dl download stedolan/jq -p "*.exe" --no-cache

# 调整并发数
gh-dl download stedolan/jq -p "*.exe" -j 8

# 通过 GitHub URL 指定仓库
gh-dl download https://github.com/stedolan/jq -p "*.exe"
```

输出目录结构：

```
# 默认层级模式                       # --flat 扁平模式
downloads/                           downloads/
  stedolan/                            jq-win64.exe
    jq/                                jq-linux64
      jq-1.8.1/                        jq-osx-amd64
        jq-win64.exe
        jq-linux64
        jq-osx-amd64
```

### 2. 配置文件模式：批量下载

适用于需要定期下载多个仓库的场景。先用 `init` 生成模板，编辑后一次执行。

```bash
# 生成示例配置文件
gh-dl init my-repos.json

# 编辑后批量下载
gh-dl config my-repos.json

# 覆盖输出目录和并发数
gh-dl config repos.json -o ./all-downloads -j 8

# 模拟运行
gh-dl config repos.json --dry-run
```

### 3. 交互式模式：引导式下载

不带参数直接运行，按提示输入仓库、模式和版本，适合临时或一次性使用。

```bash
gh-dl
```

交互过程将逐一询问仓库、模式和版本，完成后确认即开始下载。

## 配置文件格式

### JSON

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
    },
    {
      "owner": "neovim",
      "repo": "neovim",
      "pattern": "*.appimage"
    }
  ]
}
```

### YAML

需要安装 `pyyaml`：

```bash
pip install pyyaml
```

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
```

### 字段说明

| 字段      | 必填 | 类型   | 默认值     | 说明                             |
|-----------|------|--------|------------|----------------------------------|
| `owner`   | 是   | string | -          | GitHub 用户或组织名              |
| `repo`    | 是   | string | -          | 仓库名                           |
| `pattern` | 是   | string | -          | Glob 模式，如 `*.exe`、`*linux*` |
| `version` | 否   | string | `latest`   | Release 标签                     |
| `output`  | 否   | string | 当前目录   | 下载输出路径                     |

## CLI 参考

### 全局选项

| 选项           | 说明             |
|----------------|------------------|
| `--version`    | 显示版本号并退出 |
| `-h`, `--help` | 显示帮助信息     |

### download 子命令

从单个仓库下载资源。

```
gh-dl download <repo> -p <pattern> [options]
```

| 参数                        | 说明                                        |
|-----------------------------|---------------------------------------------|
| `repo`                      | 仓库标识，格式 `owner/repo` 或完整 GitHub URL |
| `--pattern`, `-p`           | 匹配模式（可重复，必填）                    |
| `--regex`                   | 将 `--pattern` 解释为正则表达式而非 glob    |
| `--version`, `-v`           | 版本标签，默认 `latest`                     |
| `--output`, `-o`            | 输出目录，默认 `./downloads`                |
| `--flat`                    | 扁平输出，不创建 `owner/repo/version` 层级  |
| `--dry-run`                 | 模拟运行，不实际写入文件                    |
| `--concurrent`, `-j`        | 并发下载数，默认 4                          |
| `--no-cache`                | 跳过缓存，强制重新下载                      |

### config 子命令

根据配置文件批量下载多个仓库。

```
gh-dl config <config_file> [options]
```

| 参数                        | 说明                                        |
|-----------------------------|---------------------------------------------|
| `config_file`               | 配置文件路径（JSON 或 YAML）                |
| `--output`, `-o`            | 全局输出目录覆盖                            |
| `--flat`                    | 全局扁平输出覆盖                            |
| `--dry-run`                 | 模拟运行                                    |
| `--concurrent`, `-j`        | 并发下载数，默认 4                          |
| `--regex`                   | 将 `--pattern` 解释为正则表达式而非 glob    |

### init 子命令

生成示例配置文件。

```
gh-dl init [output]
```

| 参数     | 说明                           |
|----------|--------------------------------|
| `output` | 输出路径，默认 `gh-dl-config.json` |
| `--user`, `-u` | 生成用户配置文件（`.gh-dl.json`）而非仓库配置文件 |

### 用户配置（持久化 Token / 代理）

每次打开终端都要设置 `GITHUB_TOKEN` 和代理很麻烦。gh-dl 支持从 JSON 文件自动加载：

```bash
# 生成示例用户配置文件
gh-dl init --user
```

编辑生成的 `.gh-dl.json`：

```json
{
  "github_token": "ghp_your_token_here",
  "http_proxy": "http://127.0.0.1:7890",
  "https_proxy": "http://127.0.0.1:7890"
}
```

保存后，**每次运行 gh-dl 自动加载**，无需再设环境变量。

**搜索顺序**（优先级从高到低）：

| 路径 | 说明 |
|------|------|
| `.gh-dl.json` | 当前目录（项目级） |
| `%APPDATA%/gh-dl/config.json` | Windows 用户配置 |
| `~/.config/gh-dl/config.json` | Linux/macOS 用户配置 |
| `~/.gh-dl.json` | 用户目录 |

> 环境变量优先级高于配置文件。同时设了环境变量和配置文件时，以环境变量为准。

### list 子命令

列出仓库指定版本中的可下载资源。

```
gh-dl list <repo> [options]
```

| 参数                  | 说明                                |
|-----------------------|-------------------------------------|
| `repo`                | 仓库标识，格式 `owner/repo` 或完整 URL |
| `--version`, `-v`     | 版本标签，默认 `latest`             |

## 退出码

| 退出码 | 含义                                              |
|--------|---------------------------------------------------|
| `0`    | 全部下载成功                                      |
| `1`    | 部分下载失败（至少一个资源失败，其他成功）        |
| `2`    | 严重错误（配置文件错误、API 错误、文件未找到、用户中断） |

## 技巧与常见问题

### 认证与 Rate Limit

GitHub API 未认证请求每小时仅限 60 次。设置 `GITHUB_TOKEN` 可提升至 5000 次/小时。Token 从 [GitHub Settings](https://github.com/settings/tokens) 生成，无需任何权限即可访问公开仓库。

```bash
# Linux / macOS
export GITHUB_TOKEN="ghp_your_token_here"

# Windows PowerShell
$env:GITHUB_TOKEN = "ghp_your_token_here"

# Windows CMD（注意：**不要加引号**）
set GITHUB_TOKEN=ghp_your_token_here

# 验证环境变量
echo $GITHUB_TOKEN              # Linux/macOS
echo $env:GITHUB_TOKEN          # Windows PowerShell
echo %GITHUB_TOKEN%             # Windows CMD
```

> **⚠️ CMD 引号陷阱**：在 CMD 里 `set VAR="value"` 会把双引号也算进值里。
> 请用 `set VAR=value`（不加引号）。如果不小心加了引号，gh-dl 会自动去除首尾引号。

API 错误 `GitHub API rate limit exceeded` 出现时，设置 Token 即可解决。

### 没有找到匹配的文件

用 `gh-dl list` 查看 Release 中的实际文件名，根据输出调整 `--pattern` 参数。

```bash
gh-dl list owner/repo
```

### 下载多个模式

通过多个 `-p` 参数组合不同模式：

```bash
gh-dl download neovim/neovim -p "*.zip" -p "*.appimage" -p "*.gz"
```

### 使用正则表达式筛选

默认模式使用 glob 语法，添加 `--regex` 后使用正则表达式匹配：

```bash
# 下载所有 .zip 文件（正则写法）
gh-dl download neovim/neovim -p ".*\.zip$" --regex

# 下载 Windows 相关文件（64位或ARM64）
gh-dl download neovim/neovim -p "win(64|arm64)" --regex

# 组合多个正则模式
gh-dl download neovim/neovim -p ".*\.(msi|exe)$" -p "win64.*" --regex
```

正则匹配使用 `re.search()`，只要模式在文件名中任意位置匹配即命中。不加 `--regex` 时行为不变，完全向后兼容。

### 批量下载后打包

```bash
gh-dl config repos.json --output ./archives
tar -czf releases.tar.gz ./archives
```

### 代理配置

gh-dl 基于 `requests` 库，支持标准环境变量代理：

```bash
# Linux / macOS
export HTTP_PROXY="http://127.0.0.1:7890"
export HTTPS_PROXY="http://127.0.0.1:7890"

# Windows PowerShell
$env:HTTP_PROXY = "http://127.0.0.1:7890"
$env:HTTPS_PROXY = "http://127.0.0.1:7890"

# Windows CMD（注意：**不要加引号**）
set HTTP_PROXY=http://127.0.0.1:7890
set HTTPS_PROXY=http://127.0.0.1:7890
```

> **⚠️ CMD 引号陷阱**：在 CMD 里 `set VAR="value"` 会把双引号也算进值里，导致 `requests`
> 解析出类似 `'"http'` 的错误代理主机名。CMD 设置环境变量**不要加引号**。
> PowerShell 和 bash 则需要加引号。

```bash
gh-dl download owner/repo -p "*.zip"
```

SOCKS 代理需要额外安装依赖：

```bash
pip install requests[socks]
```

```bash
# PowerShell
$env:HTTP_PROXY = "socks5://127.0.0.1:1080"
$env:HTTPS_PROXY = "socks5://127.0.0.1:1080"
```

### 私有仓库

访问私有仓库需要设置 `GITHUB_TOKEN`，且 Token 必须拥有 `repo` 权限范围。

### 下载被中断

重新运行相同命令即可。gh-dl 自动检测 `.part` 临时文件并断点续传，已完成部分不会重复下载。

### Windows 路径过长

嵌套层级较深时可能触发 MAX_PATH 限制（260 字符）。使用 `--flat` 跳过层级，或将输出目录设在根目录（如 `C:\downloads\`）。

### Unicode 文件名乱码

Windows 终端默认编码可能非 UTF-8：

```bash
$env:PYTHONIOENCODING = "utf-8"   # PowerShell
chcp 65001                         # 或切换代码页
```

## 开发

### 安装开发依赖

```bash
pip install -e ".[dev]"
```

### 运行测试

```bash
# 运行所有测试
pytest

# 带详细输出
pytest -v

# 指定测试文件或函数
pytest tests/test_downloader.py -v
pytest tests/test_utils.py::test_safe_filename -v
```

### 项目结构

```
gh-downloader/
  gh_downloader/        # 核心代码
  tests/                # 测试代码
  pyproject.toml        # 项目配置与依赖
  README.md
```

## 许可

MIT License
