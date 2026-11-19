# gh-dl: 批量 GitHub / GitLab Release 下载工具

gh-dl 是一个命令行工具，用于从 GitHub 或 GitLab 的 Release 页面批量下载资源文件。它支持 glob 或正则表达式文件筛选、并发下载、智能缓存、断点续传，以及 CLI、配置文件、交互式三种操作模式。你不需要逐页手动点击，一条命令就能取回任意仓库的发布包，也可以把常用配置写进文件，之后反复执行。

## 目录

- [功能特性](#功能特性)
- [安装](#安装)
- [快速上手](#快速上手)
- [使用模式](#使用模式)
  - [CLI 模式：直接下载](#cli-模式直接下载)
  - [配置文件模式：批量下载](#配置文件模式批量下载)
  - [交互式模式](#交互式模式)
- [配置](#配置)
  - [用户配置（全局与项目级）](#用户配置全局与项目级)
  - [仓库配置文件](#仓库配置文件)
- [GitLab 支持](#gitlab-支持)
- [CLI 参考](#cli-参考)
  - [download 子命令](#download-子命令)
  - [config 子命令](#config-子命令)
  - [init 子命令](#init-子命令)
  - [list 子命令](#list-子命令)
- [退出码](#退出码)
- [技巧与常见问题](#技巧与常见问题)
- [开发](#开发)
- [许可](#许可)

## 功能特性

- 三种操作模式：CLI 直接下载、配置文件批量下载、交互式引导下载
- 并发下载：线程池同时下载，默认 4 个并发，通过 `-j` 调整
- 灵活的模式匹配：Glob（`fnmatch`，如 `*.exe`）与正则表达式（`--regex`，如 `.*\.exe$`）
- 实时进度条：显示 `[####------------]` 进度条、百分比和下载速度
- 智能缓存：文件已存在且大小一致时自动跳过
- 断点续传：中断后重新运行，从 `.part` 临时文件继续下载，并对服务端返回值做一致性校验
- 多实例安全：输出目录级文件锁，两个窗口同时执行同一命令时后者等待并复用缓存，不会共用临时文件
- 自动重试：失败最多重试 3 次，指数退避间隔为 1 秒、3 秒、9 秒
- 文件名安全：Windows 不合法字符（`\ / : * ? " < > |`）自动替换为下划线
- 优雅退出：Ctrl+C 安全中断，自动清理临时文件
- 版本选择：支持最新版（`latest`）或指定标签版本
- 扁平输出：可选择不创建 `owner/repo/version` 层级目录
- 模拟运行：`--dry-run` 预览将下载的文件，不实际写入
- 认证支持：通过环境变量或用户配置文件提供 Token，提升 API 限频配额
- 全局限速：`--limit-rate` 设置所有并发线程共享的总带宽上限，支持 `500K`、`2M` 等写法
- 镜像加速：`--ghproxy` 用 ghproxy 风格的 URL 前缀改写 GitHub 下载地址，只影响下载，API 请求仍直连
- 多平台支持：GitHub 与 GitLab，包含自建 GitLab 和嵌套 group 命名空间
- 持久配置：Token 和代理设置写入文件，无需每个终端重新设置

> **定位**：与 `gh release download` 和 `yuler/gh-download` 相比，本工具的差异主要在 GitLab 支持（含自建实例、嵌套 group）与配置文件批量下载。只下载单个 GitHub 仓库的少量文件时，官方 `gh` 命令已经够用。

## 安装

### 环境要求

- Python >= 3.10
- 运行时依赖：`requests`

### 从源码安装

```bash
pip install -e .
gh-dl --version    # 输出: gh-dl 0.1.0
```

### 命令别名

`gh-dl` 和 `ghdl` 完全等价，两条命令指向同一个程序，按习惯任选其一。例如 `ghdl neovim/neovim -p "*.zip"` 与 `gh-dl neovim/neovim -p "*.zip"` 效果相同。

PyInstaller 打包（`pyinstaller gh-dl.spec`）会同时产出 `gh-dl.exe` 和 `ghdl.exe`。

## 快速上手

```bash
# 列出资源，确认实际文件名
gh-dl list neovim/neovim

# 下载所有 .zip 文件到 ./nvim
# download 子命令可以省略，直接写仓库即可
gh-dl neovim/neovim -p "*.zip" -o ./nvim

# 模拟运行，不实际写入
gh-dl neovim/neovim -p "*.zip" --dry-run
```

`gh-dl list` 的典型输出：

```text
Assets for stedolan/jq (jq-1.8.1):
  jq-win64.exe                                              1.00 MB
  -> https://github.com/stedolan/jq/releases/download/jq-1.8.1/jq-win64.exe
  jq-linux64                                                1.95 MB
  -> https://github.com/stedolan/jq/releases/download/jq-1.8.1/jq-linux64
  jq-osx-amd64                                              1.00 MB
  -> https://github.com/stedolan/jq/releases/download/jq-1.8.1/jq-osx-amd64
```

下载时的典型输出：

```text
$ gh-dl download stedolan/jq -p "*linux*" -p "*.exe"

  jq-linux64: [####################] 100% 1.95 MB/1.95 MB 2.3 MB/s
  jq-linux-arm: [####################] 100% 1.71 MB/1.71 MB 1.9 MB/s
  jq-win64.exe: [####################] 100% 1.00 MB/1.00 MB 1.5 MB/s
Done: 3 downloaded, 0 cached, 0 errors
```

> **省略 download**：只要第一个参数是仓库（`owner/repo` 或完整 GitHub URL），`gh-dl <repo>` 就等价于 `gh-dl download <repo>`。例如 `gh-dl stedolan/jq -p "*.exe"` 与 `gh-dl download stedolan/jq -p "*.exe"` 完全一致。其他子命令（`list`、`config`、`init`）仍需显式写出。不带任何参数运行时进入交互模式。

## 使用模式

### CLI 模式：直接下载

适合一次性下载单个仓库。`download` 也是默认子命令，可以省略。

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

默认输出目录为 `./downloads`，目录结构如下：

```text
# 默认层级模式                       # --flat 扁平模式
downloads/                           downloads/
  stedolan/                            jq-win64.exe
    jq/                                jq-linux64
      jq-1.8.1/                        jq-osx-amd64
        jq-win64.exe
        jq-linux64
        jq-osx-amd64
```

### 配置文件模式：批量下载

需要定期下载多个仓库时，把仓库写进配置文件，一次执行。先用 `init` 生成模板，编辑后运行 `config`。

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

配置文件的字段定义见 [仓库配置文件](#仓库配置文件)。

### 交互式模式

不带任何参数直接运行，按提示依次输入仓库、模式和版本，确认后开始下载。适合临时或一次性使用。

```bash
gh-dl
```

只要带了仓库参数，就会直接下载，不会进入交互模式。

## 配置

gh-dl 有两类配置文件：**用户配置**保存 Token、代理等跨会话设置，**仓库配置文件**描述要批量下载的仓库列表。两者用途不同，互不替代。

### 用户配置（全局与项目级）

每次打开终端都设置 Token 和代理很麻烦。gh-dl 会自动加载 JSON 格式的用户配置，保存后每次运行都生效。

#### 生成配置

```bash
# 项目级：在当前目录生成 .gh-dl.json
gh-dl init --user

# 全局：生成用户级配置
gh-dl init --global
```

两种方式都使用固定路径；`--global` 不接受额外输出路径。

| 范围 | 命令 | 生成路径 |
|------|------|----------|
| 项目级 | `gh-dl init --user`（别名 `-u`） | 当前目录下的 `./.gh-dl.json` |
| 全局（Windows） | `gh-dl init --global` | `%APPDATA%\gh-dl\config.json` |
| 全局（Linux / macOS） | `gh-dl init --global` | `$XDG_CONFIG_HOME/gh-dl/config.json`；未设置 `XDG_CONFIG_HOME` 时为 `~/.config/gh-dl/config.json` |

两个命令都不会覆盖已存在的文件。需要覆盖时显式加上 `--force`：

```bash
gh-dl init --global --force
```

`--global` 不能和显式输出路径一起使用。`gh-dl init --global output.json` 是无效用法，程序会以退出码 `2` 结束。

#### 配置示例

```json
{
  "github_token": "ghp_your_token_here",
  "github_api_url": "https://api.github.com",
  "gitlab_token": "glpat_your_token_here",
  "gitlab_url": "https://gitlab.com",
  "http_proxy": "http://127.0.0.1:7890",
  "https_proxy": "http://127.0.0.1:7890",
  "download_sources": true,
  "limit_rate": "",
  "ghproxy": ""
}
```

#### 支持的键

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `github_token` | string | 无 | GitHub 个人访问令牌，用于提升 API 限频配额和访问私有仓库 |
| `github_api_url` | string | `https://api.github.com` | GitHub API 基址，例如 GitHub Enterprise |
| `gitlab_token` | string | 无 | GitLab 个人访问令牌，用于访问私有项目和提升配额 |
| `gitlab_url` | string | `https://gitlab.com` | GitLab 站点基址，自建实例改为自己的地址 |
| `http_proxy` | string | 无 | HTTP 代理地址 |
| `https_proxy` | string | 无 | HTTPS 代理地址 |
| `download_sources` | bool | `true` | 是否默认下载源码包；设为 `false` 时默认跳过源码包 |
| `limit_rate` | string / int | 无 | 全局限速值，如 `"500K"`、`"2M"`，`0` 表示不限速；等价于 `--limit-rate` |
| `ghproxy` | string | 无 | ghproxy 风格的镜像前缀，如 `"https://ghfast.top"`；等价于 `--ghproxy`，也可由环境变量 `GH_PROXY` 提供 |

#### 搜索顺序

按顺序查找，**第一个存在的文件生效**：

| 顺序 | 路径 | 说明 |
|------|------|------|
| 1 | `./.gh-dl.json` | 当前目录，项目级配置 |
| 2 | Windows：`%APPDATA%\gh-dl\config.json`；Linux / macOS：`$XDG_CONFIG_HOME/gh-dl/config.json`（未设置时为 `~/.config/gh-dl/config.json`） | 全局用户配置 |
| 3 | `~/.gh-dl.json` | 用户目录下的旧式路径 |

#### 环境变量优先级

环境变量（`GITHUB_TOKEN`、`GITLAB_TOKEN`、`HTTP_PROXY`、`HTTPS_PROXY`）优先级高于配置文件。同时设置时以环境变量为准。

`GH_PROXY` 的优先级与上面相反：`--ghproxy` 高于用户配置的 `ghproxy`，用户配置又高于环境变量 `GH_PROXY`。即命令行参数 > 用户配置 > 环境变量。

### 仓库配置文件

仓库配置文件由 `gh-dl init` 生成，也可以手写。支持 JSON 和 YAML 两种格式，按文件扩展名自动识别。

#### JSON

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

#### YAML

YAML 支持需要安装 `pyyaml`：

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

#### 字段说明

| 字段 | 必填 | 类型 | 默认值 | 说明 |
|------|------|------|--------|------|
| `owner` | 是 | string | 无 | GitHub 用户、组织名，或 GitLab 命名空间 |
| `repo` | 是 | string | 无 | 仓库名 |
| `pattern` | 是 | string | 无 | Glob 模式（加 `--regex` 时按正则解释），如 `*.exe`、`*linux*` |
| `version` | 否 | string | `latest` | Release 标签 |
| `output` | 否 | string | `./downloads` | 下载输出路径 |
| `sources` | 否 | bool | `true` | 是否同时下载源码包 |
| `provider` | 否 | string | 自动判断 | `github` 或 `gitlab` |

## GitLab 支持

gh-dl 支持 GitLab.com 和自建 GitLab 实例，GitHub 的用法完全不变。

```bash
# GitLab.com：下载最新 Release 中的 zip 资源
gh-dl https://gitlab.com/group/project -p "*.zip"

# 嵌套 group 命名空间
gh-dl https://gitlab.com/group/sub/project -p "*.zip"

# 指定 Release 版本（GitLab 使用 /-/releases/ 路径）
gh-dl https://gitlab.com/group/project/-/releases/v1.2.3 -p "*.zip"

# 最新版本（permalink 形式）
gh-dl list https://gitlab.com/group/project/-/releases/permalink/latest

# 自建 GitLab（API 基址为 <url>/api/v4）
gh-dl download https://gitlab.example.com/team/project -p "*.zip" \
  --gitlab-url https://gitlab.example.com

# 也可以显式指定提供方
gh-dl download group/project -p "*.zip" \
  --provider gitlab --gitlab-url https://gitlab.example.com
```

### 认证

GitLab 认证使用个人访问令牌（Personal Access Token），通过 `PRIVATE-TOKEN` 请求头发送。设置环境变量 `GITLAB_TOKEN`，或在用户配置中写入 `gitlab_token`（键的完整说明见 [用户配置](#用户配置全局与项目级)）。访问私有项目时，Token 需要具备 `read_api`、`read_repository` 等相应权限。

```bash
# Linux / macOS
export GITLAB_TOKEN="glpat-xxxxxxxxxxxx"

# Windows PowerShell
$env:GITLAB_TOKEN = "glpat-xxxxxxxxxxxx"
```

### 资源与缓存行为

GitLab Release 的源码包（`assets.sources`，如 zip、tar.gz、tar.bz2、tar）以及 Release asset links（`assets.links`）都会被识别并下载。需要注意的是，GitLab 的 asset link 不返回文件大小，因此基于大小的缓存校验会退化为“文件存在即视为已缓存”。需要强制重新下载时使用 `--no-cache`。

### 可用选项

| 选项 | 说明 |
|------|------|
| `--provider` | 显式指定 `github` 或 `gitlab`（默认根据 URL 自动判断） |
| `--gitlab-url` | 自建 GitLab 的站点地址（默认 `https://gitlab.com`） |
| `--github-api-url` | GitHub API 基址，例如 GitHub Enterprise（默认官方 API） |

## CLI 参考

### 全局选项

| 选项 | 说明 |
|------|------|
| `--version` | 显示版本号并退出 |
| `-h`, `--help` | 显示帮助信息 |

### download 子命令

从单个仓库下载资源。`download` 是默认子命令，可以省略。

```text
gh-dl download <repo> [-p <pattern> ...] [options]
gh-dl <repo> [-p <pattern> ...] [options]      # 等价简写
```

| 参数 | 说明 |
|------|------|
| `repo` | 仓库标识，格式 `owner/repo` 或完整 GitHub/GitLab URL |
| `--pattern`, `-p` | 匹配模式（可重复；不指定则下载全部文件） |
| `--regex` | 将 `--pattern` 解释为正则表达式而非 glob |
| `--version`, `-v` | 版本标签，默认 `latest` |
| `--output`, `-o` | 输出目录，默认 `./downloads` |
| `--flat` | 扁平输出，不创建 `owner/repo/version` 层级 |
| `--dry-run` | 模拟运行，不实际写入文件 |
| `--concurrent`, `-j` | 并发下载数，默认 4 |
| `--limit-rate` | 总带宽上限（所有并发线程共享），如 `500K`、`2M`、`1.5M`、`10 MB/s`；`0` 表示不限速，默认不限速 |
| `--ghproxy` | ghproxy 风格的 GitHub 下载镜像前缀，如 `https://ghfast.top`；只改写下载地址，API 请求仍直连 GitHub |
| `--no-cache` | 跳过缓存，强制重新下载 |
| `--no-sources` | 跳过源码包（源码包默认下载） |
| `--provider` | `github` 或 `gitlab`（默认自动判断） |
| `--gitlab-url` | 自建 GitLab 站点地址 |
| `--github-api-url` | GitHub API 基址（如 GitHub Enterprise） |

### config 子命令

根据配置文件批量下载多个仓库。

```text
gh-dl config <config_file> [options]
```

| 参数 | 说明 |
|------|------|
| `config_file` | 配置文件路径（JSON 或 YAML） |
| `--output`, `-o` | 全局输出目录覆盖 |
| `--flat` | 全局扁平输出覆盖 |
| `--dry-run` | 模拟运行 |
| `--concurrent`, `-j` | 并发下载数，默认 4 |
| `--limit-rate` | 总带宽上限（所有并发线程共享），如 `500K`、`2M`、`1.5M`、`10 MB/s`；`0` 表示不限速，默认不限速；每个仓库分别计算 |
| `--ghproxy` | ghproxy 风格的 GitHub 下载镜像前缀，如 `https://ghfast.top`；只改写下载地址，API 请求仍直连 GitHub |
| `--regex` | 将配置文件中的 `pattern` 解释为正则表达式而非 glob |
| `--no-sources` | 跳过源码包 |
| `--provider` | `github` 或 `gitlab`（默认自动判断） |
| `--gitlab-url` | 自建 GitLab 站点地址 |
| `--github-api-url` | GitHub API 基址（如 GitHub Enterprise） |

### init 子命令

生成示例配置文件。默认生成仓库配置文件；`--user` 或 `--global` 生成用户配置。

```text
gh-dl init [output] [--user] [--global] [--force]
```

| 参数 | 说明 |
|------|------|
| `output` | 仓库配置文件的输出路径，默认 `gh-dl-config.json` |
| `--user`, `-u` | 在当前目录生成用户配置 `./.gh-dl.json` |
| `--global` | 生成全局用户配置；路径见 [用户配置](#用户配置全局与项目级)。不能与 `output` 同时使用 |
| `--force` | 覆盖已存在的文件（用于 `--user` / `--global` 生成的文件）；不加时拒绝覆盖 |

`--global` 与显式输出路径同时出现属于无效用法，退出码为 `2`。

### list 子命令

列出仓库指定版本中的可下载资源。

```text
gh-dl list <repo> [options]
```

| 参数 | 说明 |
|------|------|
| `repo` | 仓库标识，格式 `owner/repo` 或完整 GitHub/GitLab URL |
| `--version`, `-v` | 版本标签，默认 `latest` |
| `--no-sources` | 列表不显示源码包 |
| `--provider` | `github` 或 `gitlab`（默认自动判断） |
| `--gitlab-url` | 自建 GitLab 站点地址 |
| `--github-api-url` | GitHub API 基址（如 GitHub Enterprise） |

## 退出码

| 退出码 | 含义 |
|--------|------|
| `0` | 全部下载成功 |
| `1` | 部分下载失败（至少一个资源失败，其他成功） |
| `2` | 严重错误（配置文件错误、API 错误、文件未找到、用户中断、无效参数组合） |

## 技巧与常见问题

### 认证与 Rate Limit

GitHub API 未认证请求每小时仅限 60 次。设置 `GITHUB_TOKEN` 可提升至 5000 次/小时。Token 从 [GitHub Settings](https://github.com/settings/tokens) 生成，访问公开仓库无需任何权限范围。

```bash
# Linux / macOS
export GITHUB_TOKEN="ghp_your_token_here"

# Windows PowerShell
$env:GITHUB_TOKEN = "ghp_your_token_here"

# Windows CMD（不要加引号）
set GITHUB_TOKEN=ghp_your_token_here

# 验证环境变量
echo $GITHUB_TOKEN              # Linux/macOS
echo $env:GITHUB_TOKEN          # Windows PowerShell
echo %GITHUB_TOKEN%             # Windows CMD
```

> **CMD 引号陷阱**：在 CMD 里 `set VAR="value"` 会把双引号也算进值里。请使用 `set VAR=value`（不加引号）。如果不小心加了引号，gh-dl 会自动去除首尾引号。

出现 `GitHub API rate limit exceeded` 时，设置 Token 即可解决。

### 没有找到匹配的文件

用 `gh-dl list` 查看 Release 中的实际文件名，再据此调整 `--pattern`。

```bash
gh-dl list owner/repo
```

### 下载多个模式

通过多个 `-p` 参数组合不同模式：

```bash
gh-dl download neovim/neovim -p "*.zip" -p "*.appimage" -p "*.gz"
```

### 使用正则表达式筛选

默认使用 glob 语法，添加 `--regex` 后按正则表达式匹配：

```bash
# 下载所有 .zip 文件（正则写法）
gh-dl download neovim/neovim -p ".*\.zip$" --regex

# 下载 Windows 相关文件（64 位或 ARM64）
gh-dl download neovim/neovim -p "win(64|arm64)" --regex

# 组合多个正则模式
gh-dl download neovim/neovim -p ".*\.(msi|exe)$" -p "win64.*" --regex
```

正则匹配使用 `re.search()`，只要模式在文件名任意位置命中即可。不加 `--regex` 时行为不变。

### 批量下载后打包

```bash
gh-dl config repos.json --output ./archives
tar -czf releases.tar.gz ./archives
```

### 限速与镜像加速

`--limit-rate` 限制所有并发线程共享的总带宽，常见写法有 `500K`、`1M`、`1.5M`、`10 MB/s`，`0` 表示不限速。默认不限速。

```bash
# 单个仓库限速 500 KB/s
gh-dl neovim/neovim -p "*.zip" --limit-rate 500K
```

批量下载时同样可以限速，每个仓库分别计算额度：

```bash
gh-dl config repos.json --limit-rate 1M
```

`--ghproxy` 用 ghproxy 风格的镜像前缀改写 GitHub 下载地址：

```bash
gh-dl neovim/neovim -p "*.zip" --ghproxy https://ghfast.top
```

它只改写 Release 资源与源码包的下载链接，API 请求仍然直连 GitHub。镜像只对 GitHub 公开仓库的下载地址生效；私有仓库下载和 API 请求都不经过镜像，因此镜像场景下不会携带 Token。

想长期生效，可以写进用户配置：

```json
{
  "ghproxy": "https://ghfast.top",
  "limit_rate": "500K"
}
```

也可以用环境变量提供镜像前缀，优先级为命令行参数 > 用户配置 > 环境变量：

```bash
# Linux / macOS
export GH_PROXY="https://ghfast.top"

# Windows PowerShell
$env:GH_PROXY = "https://ghfast.top"

# Windows CMD（不要加引号）
set GH_PROXY=https://ghfast.top
```

> **与代理的区别**：`HTTP_PROXY` / `HTTPS_PROXY` 是 HTTP 代理，整个请求经由代理转发；`--ghproxy` 只是把 GitHub 下载 URL 换成镜像地址，两者用途不同，可以同时使用。

### 代理配置

gh-dl 基于 `requests`，支持标准环境变量代理：

```bash
# Linux / macOS
export HTTP_PROXY="http://127.0.0.1:7890"
export HTTPS_PROXY="http://127.0.0.1:7890"

# Windows PowerShell
$env:HTTP_PROXY = "http://127.0.0.1:7890"
$env:HTTPS_PROXY = "http://127.0.0.1:7890"

# Windows CMD（不要加引号）
set HTTP_PROXY=http://127.0.0.1:7890
set HTTPS_PROXY=http://127.0.0.1:7890
```

> **CMD 引号陷阱**：在 CMD 里 `set VAR="value"` 会把双引号也算进值里，导致 `requests` 解析出类似 `'"http'` 的错误代理主机名。CMD 设置环境变量不要加引号，PowerShell 和 bash 则需要加引号。

也可以把代理写进用户配置的 `http_proxy` / `https_proxy`，避免每次手动设置，详见 [用户配置](#用户配置全局与项目级)。

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

访问 GitHub 私有仓库需要设置 `GITHUB_TOKEN`，且 Token 必须拥有 `repo` 权限范围。GitLab 私有项目使用 `GITLAB_TOKEN`，权限要求见 [GitLab 支持](#gitlab-支持)。

### 下载被中断

重新运行相同命令即可。gh-dl 会自动检测 `.part` 临时文件并断点续传，已完成部分不会重复下载。续传前会校验服务端返回的状态码与 `Content-Range` 起点：若服务器忽略了 `Range` 返回 200，或返回 416，则丢弃旧临时文件从头下载，避免拼接出损坏文件。

### 多窗口同时执行同一命令

gh-dl 对每个输出目录加了一把跨进程文件锁（`.gh-dl.lock`）。两个窗口同时下载到同一个 `--output` 目录时：

- 先启动的进程正常下载；
- 后启动的进程在 stderr 打印等待提示，然后阻塞等待，不会与前者共用 `.part` 文件；
- 前者结束后，后者拿到锁，智能缓存判定文件已完整，直接跳过并输出 `0 downloaded, N cached`；
- 锁由操作系统在进程退出（包括崩溃、Ctrl+C）时自动释放，`.gh-dl.lock` 文件本身会保留，无需手动删除。

`--dry-run` 不写文件，因此不加锁。不同 `--output` 目录之间互不影响；同一目录下下载不同仓库会串行执行，这是为了保证缓存判断和落盘是原子的。

### Windows 路径过长

嵌套层级较深时可能触发 MAX_PATH 限制（260 字符）。使用 `--flat` 跳过层级，或把输出目录设在根目录（如 `C:\downloads\`）。

### Unicode 文件名乱码

Windows 终端默认编码可能不是 UTF-8：

```bash
$env:PYTHONIOENCODING = "utf-8"   # PowerShell
chcp 65001                         # 或切换代码页
```

## 开发

### 安装开发依赖

```bash
pip install -e ".[dev]"
```

开发依赖包括 `pytest` 和 `respx`。

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

```text
gh-downloader/
  gh_downloader/        # 核心代码
  tests/                # 测试代码
  pyproject.toml        # 项目配置与依赖
  README.md
```

## 许可

MIT License