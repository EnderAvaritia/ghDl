# GitHub Release 批量下载器 (gh-dl)

## TL;DR

> **Quick Summary**: 构建一个 Python CLI 工具 `gh-dl`，支持从 GitHub Releases 批量下载资产文件，支持通配符 glob 匹配、用户指定版本、三种操作模式（配置文件/CLI参数/交互式）、并发下载加速。
>
> **Deliverables**:
> - `gh_downloader/` Python 包（6个模块）
> - `pyproject.toml` 项目配置（pip 可安装）
> - `tests/` pytest 测试套件
> - 可执行命令 `gh-dl`
>
> **Estimated Effort**: Medium-Large（约 2000-3000 行代码）
> **Parallel Execution**: YES - 4 waves
> **Critical Path**: Task 1 → Task 4 → Task 6 → Task 8 → F1-F4

---

## Context

### Original Request
用户需要工具来批量下载 GitHub Release 中的文件，支持通配符模式匹配，作为长期收集发行文件的习惯。

### Interview Summary
**Key Discussions**:
- **技术栈**: Python 3.12, requests (已预装), argparse, stdlib first
- **三种模式**: 配置文件(JSON+YAML) / CLI参数 / 交互式(极简)
- **并发下载**: 多文件同时下载加速
- **安装方式**: pip 包 (`gh-dl` 命令)
- **范围**: 支持私有仓库(GITHUB_TOKEN), 不包含自动解压/通知/定时
- **测试**: pytest, 每个模块有对应测试

**Research Findings**:
- Python 3.12.4 + pip 24.0 已就绪，requests 已预装
- 目录为空，适合新建项目
- 参考了类似项目: github_release_downloader, dra, gh CLI

### Metis Review
**Identified Gaps** (addressed):
- **目录结构**: 支持可选 `--flat` 和默认分层
- **并发策略**: 用户选择并发下载
- **Token管理**: 仅 `GITHUB_TOKEN` 环境变量
- **重试机制**: 3次指数退避重试
- **Ctrl+C处理**: 优雅中断，清理临时文件

---

## Work Objectives

### Core Objective
构建一个 Python CLI 工具 `gh-dl`，通过 GitHub Releases API 批量下载与 glob 通配符匹配的资产文件，支持三种操作模式。

### Concrete Deliverables
- `gh_downloader/__init__.py` - 包声明
- `gh_downloader/__main__.py` - `python -m gh_downloader` 入口
- `gh_downloader/cli.py` - CLI 参数解析（argparse）
- `gh_downloader/config.py` - JSON/YAML 配置解析
- `gh_downloader/api.py` - GitHub API 交互客户端
- `gh_downloader/downloader.py` - 下载引擎（并发/重试/缓存/断点续传）
- `gh_downloader/interactive.py` - 交互式模式（极简 input）
- `gh_downloader/utils.py` - 工具函数（路径/编码/信号处理）
- `pyproject.toml` - 项目元数据 + 入口点
- `tests/` - pytest 测试套件

### Definition of Done
- [x] `pip install -e .` 可安装，`gh-dl --help` 显示完整帮助
- [x] `gh-dl list owner/repo --pattern "*.exe"` 成功下载
- [x] `gh-dl config repos.json` 从配置文件批量下载
- [x] `gh-dl`（无参数）进入交互模式
- [x] 并发下载正常工作
- [x] `GITHUB_TOKEN` 环境变量提升速率限制
- [x] 测试通过：`pytest tests/` → PASS
- [x] 所有退出码约定正确（0/1/2）

### Must Have
- 三种操作模式完整实现
- glob 通配符匹配（支持 `*`、`?`、`[abc]` 等标准 fnmatch 语法）
- 用户指定版本（latest 或具体 tag）
- 并发下载
- 跳过已存在文件（智能缓存，按文件名+大小比对）
- 断点续传（HTTP Range 头部）
- 自动重试（3次，指数退避）
- GITHUB_TOKEN 认证支持
- --dry-run 试运行模式
- Ctrl+C 优雅中断
- 退出码约定
- pytest 测试覆盖核心逻辑

### Must NOT Have (Guardrails)
- ❌ 自动解压下载的压缩包
- ❌ 通知系统（Slack/邮件）
- ❌ 定时调度/监控/守护进程
- ❌ 数据库持久化
- ❌ 支持 GitLab/Gitee/Bitbucket
- ❌ 上传/编辑/创建 release
- ❌ 自动更新检查

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** - ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: NO (will be set up)
- **Automated tests**: YES (TDD for core, tests-after for CLI)
- **Framework**: pytest + respx (HTTP mocking)
- **Coverage target**: Core logic (api.py, downloader.py, config.py) ≥ 80%

### QA Policy
Every task MUST include agent-executed QA scenarios. Evidence saved to `.omo/evidence/task-{N}-{scenario-slug}.{ext}`.

- **CLI/Backend**: Use Bash to run commands, assert exit codes and output
- **API mocking**: Use responses captured in tests
- **Integration**: Use actual GitHub API (against small public repos like `stedolan/jq`)

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation - parallel setup):
├── Task 1: Project scaffolding (pyproject.toml, package skeleton, venv) [quick]
├── Task 2: GitHub API client (api.py) - auth, releases, assets [deep]
├── Task 3: Configuration parser (config.py) - JSON+YAML validation [quick]
└── Task 4: Utility modules (utils.py) - paths, encoding, signals [quick]

Wave 2 (Core Logic - MAX PARALLEL):
├── Task 5: Download engine (downloader.py) - concurrent, resume, retry, cache [deep]
├── Task 6: CLI interface (cli.py) - argparse, subcommands, all flags [unspecified-high]
├── Task 7: Interactive mode (interactive.py) - input-based prompts [unspecified-high]
└── Task 8: Test infrastructure (tests/ setup + conftest) [quick]

Wave 3 (Integration + Polish):
├── Task 9: Main entry points (__init__.py, __main__.py, console_scripts) [quick]
├── Task 10: Integration testing + edge case handling [deep]
├── Task 11: Windows hardening (long paths, chcp, Ctrl+C) [unspecified-high]
└── Task 12: Documentation (README, --help text refinement) [writing]

Wave FINAL (Verification):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Real manual QA (unspecified-high)
└── Task F4: Scope fidelity check (deep)
-> Present results -> Get explicit user okay
```

### Dependency Matrix
- **1-4**: - - 5-8, 1
- **5**: 2, 4 - 9, 10, 3
- **6**: 3 - 9, 3
- **7**: 3 - 9, 3
- **8**: - - 10, 2
- **9**: 5, 6, 7 - 10, 11, 3
- **10**: 5, 8, 9 - F1-F4, 4
- **11**: 9 - F1-F4, 4
- **12**: 9 - F1-F4, 4

### Agent Dispatch Summary
- **Wave 1**: 4 tasks - T1 → `quick`, T2 → `deep`, T3 → `quick`, T4 → `quick`
- **Wave 2**: 4 tasks - T5 → `deep`, T6 → `unspecified-high`, T7 → `unspecified-high`, T8 → `quick`
- **Wave 3**: 4 tasks - T9 → `quick`, T10 → `deep`, T11 → `unspecified-high`, T12 → `writing`
- **FINAL**: 4 tasks - F1 → `oracle`, F2 → `unspecified-high`, F3 → `unspecified-high`, F4 → `deep`

---

## TODOs

- [x] 1. Project scaffolding (pyproject.toml, package skeleton, virtual environment)

  **What to do**:
  - Create `pyproject.toml` with project metadata, dependencies, and console_scripts entry point (`gh-dl = gh_downloader.__main__:main`)
  - Create package directory `gh_downloader/` with `__init__.py` (package docstring + version)
  - Create `__main__.py` as entry point
  - Create placeholder modules: `cli.py`, `config.py`, `api.py`, `downloader.py`, `interactive.py`, `utils.py` (each with minimal stub)
  - Create `tests/` directory with empty `__init__.py`
  - Create `.gitignore` (Python standard + Windows specifics)
  - Verify `pip install -e .` works and `gh-dl --help` shows basic help

  **Dependencies**:
  - `requests` (runtime, already installed)
  - `pytest` (dev, for testing)
  - `respx` (dev, for HTTP mocking)
  - No YAML library yet (will add when YAML support is needed, or use json-only fallback)

  **Must NOT do**:
  - Don't add third-party CLI libraries (argparse only for now)
  - Don't add rich/tqdm yet

  **Recommended Agent Profile**:
  - **Category**: `quick` - Standard boilerplate, no complex logic
  - **Skills**: none needed

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3, 4)
  - **Blocks**: Tasks 5, 6, 7, 9
  - **Blocked By**: None (start immediately)

  **References**:
  - Standard Python packaging: https://packaging.python.org/en/latest/tutorials/packaging-projects/
  - `pyproject.toml` example: Look at any local Python project with `[project.scripts]` section

  **Acceptance Criteria**:
  - [ ] `pip install -e .` completes without error
  - [ ] `gh-dl --help` prints help text and exits with code 0
  - [ ] `python -m gh_downloader --help` also works
  - [ ] All 6 module files exist with valid Python syntax
  - [ ] `pytest tests/` runs (0 tests collected)

  **QA Scenarios**:
  ```
  Scenario: Installation and basic help
    Tool: Bash
    Preconditions: In project root directory
    Steps:
      1. Run: pip install -e .
      2. Verify: exit code 0, success message
      3. Run: gh-dl --help
      4. Assert: output contains "usage:" and common flags (--help, --version)
    Expected Result: gh-dl command available, help displayed
    Evidence: .omo/evidence/task-1-install-help.txt

  Scenario: python -m entry point
    Tool: Bash
    Preconditions: Package installed
    Steps:
      1. Run: python -m gh_downloader --help
      2. Assert: same output as gh-dl --help
    Expected Result: Both entry points work identically
    Evidence: .omo/evidence/task-1-module-entry.txt
  ```

  **Commit**: YES
  - Message: `feat(init): scaffold project with pyproject.toml and package skeleton`
  - Files: `pyproject.toml`, `gh_downloader/*`, `tests/*`, `.gitignore`
  - Pre-commit: `pip install -e . && python -c "import gh_downloader; print(gh_downloader.__version__)"`

---

- [x] 2. GitHub API client (api.py) — core HTTP interactions with GitHub Releases API

  **What to do**:
  - Implement `GitHubClient` class wrapping GitHub REST API v3
  - Methods:
    - `get_release(repo: str, version: str)` → fetch release info (latest or by tag)
    - `get_assets(repo: str, release_id: int)` → list assets for a release
    - `get_asset_download_url(asset)` → resolve download URL
    - `download_asset(url, dest_path, resume_bytes=0)` → stream download with resume
    - `validate_token()` → check if GITHUB_TOKEN works
  - Handle authentication: `GITHUB_TOKEN` env var → Authorization header
  - Handle pagination (releases can have 30+ per page)
  - Handle rate limiting: detect 403/429, extract `X-RateLimit-Remaining` header, provide helpful message
  - Handle redirects (GitHub CDN redirects for downloads)
  - Error handling: `RepoNotFoundError`, `VersionNotFoundError`, `RateLimitError`, `NetworkError`
  - Base URL configurable (for future GitHub Enterprise support)
  - All HTTP via `requests` library
  - Test with respx (mock HTTP responses)

  **Must NOT do**:
  - Don't implement GraphQL API
  - Don't cache API responses in-memory (caching handled by downloader)

  **Recommended Agent Profile**:
  - **Category**: `deep` - Core module with API interaction, error handling, edge cases
  - **Skills**: none needed

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3, 4)
  - **Blocks**: Task 5 (downloader engine)
  - **Blocked By**: None

  **References**:
  - GitHub Releases API: https://docs.github.com/en/rest/releases/releases
  - requests library: `requests.get(url, stream=True)`, `requests.Session()`
  - respx mocking: https://github.com/lundberg/respx

  **Acceptance Criteria**:
  - [ ] `get_release("stedolan/jq", "latest")` returns valid release dict with tag_name, assets_url
  - [ ] `get_release("stedolan/jq", "jq-1.6")` returns release matching tag"jq-1.6"
  - [ ] `get_release("nonexistent/owner", "latest")` raises `RepoNotFoundError`
  - [ ] `get_release("stedolan/jq", "v999.999")` raises `VersionNotFoundError`
  - [ ] Rate limit headers parsed and exposed
  - [ ] All 3 custom exception classes defined
  - [ ] Token validation method works (both valid and missing token cases)

  **QA Scenarios**:
  ```
  Scenario: Fetch latest release successfully
    Tool: Bash
    Preconditions: Package installed, internet access
    Steps:
      1. Run: python -c "from gh_downloader import api; c = api.GitHubClient(); r = c.get_release('stedolan/jq', 'latest'); print(r['tag_name'])"
      2. Assert: output is a non-empty tag name string (e.g., "jq-1.7")
    Expected Result: Latest release info fetched from real GitHub API
    Evidence: .omo/evidence/task-2-latest-release.txt

  Scenario: Error on non-existent repo
    Tool: Bash
    Preconditions: Package installed
    Steps:
      1. Run: python -c "from gh_downloader import api; c = api.GitHubClient(); c.get_release('this-repo-does-not-exist-12345/foo', 'latest')"
      2. Assert: script exits with error mentioning "not found" or 404
    Expected Result: Clear error message, no crash
    Evidence: .omo/evidence/task-2-repo-not-found.txt

  Scenario: Token detection
    Tool: Bash
    Preconditions: GITHUB_TOKEN env var NOT set
    Steps:
      1. Run: python -c "from gh_downloader import api; c = api.GitHubClient(); print(c.is_authenticated())"
      2. Assert: prints "False"
    Expected Result: Detects no token
    Evidence: .omo/evidence/task-2-no-token.txt
  ```

  **Commit**: YES (group with Task 3, 4)
  - Message: `feat(api): implement GitHub Releases API client with auth and error handling`
  - Files: `gh_downloader/api.py`
  - Pre-commit: `python -c "from gh_downloader.api import GitHubClient; print('API module OK')"`

---

- [x] 3. Configuration parser (config.py) — JSON + YAML config file loading and validation

  **What to do**:
  - Implement `ConfigParser` class that loads repo configurations
  - Support: JSON (stdlib `json`), YAML (optional via `pyyaml`, graceful fallback)
  - Config schema:
    ```yaml
    repos:
      - owner: "owner-name"
        repo: "repo-name"
        pattern: "*.exe"         # glob pattern for asset matching
        version: "latest"         # "latest" or specific tag
        output: "./downloads"     # optional, per-repo output dir override
    ```
  - Validation:
    - Required fields: owner, repo, pattern
    - Optional: version (default latest), output (default from CLI)
    - Error on unknown fields
    - Validate pattern is valid glob
  - `validate_config(data)` → returns list of `RepoConfig` namedtuples or raises `ConfigError`
  - `load_config(path)` → auto-detect JSON vs YAML by file extension
  - `create_example_config(path)` → generate template config file
  - `RepoConfig` dataclass: owner, repo, pattern, version, output

  **Must NOT do**:
  - Don't require pyyaml to be installed (graceful fallback to JSON only)
  - Don't support nested config files
  - Don't support environment variable substitution in config

  **Recommended Agent Profile**:
  - **Category**: `quick` - Config parsing, straightforward validation
  - **Skills**: none needed

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 4)
  - **Blocks**: Tasks 6 (CLI), 7 (interactive)
  - **Blocked By**: None

  **References**:
  - `json` module: `json.load()`, `json.dump()`
  - `glob` module: `fnmatch.translate()` for validation
  - `dataclasses` module: `@dataclass`

  **Acceptance Criteria**:
  - [ ] `load_config("config.json")` correctly loads JSON config
  - [ ] `load_config("config.yaml")` correctly loads YAML config (if pyyaml installed)
  - [ ] `load_config("config.yml")` also detects YAML
  - [ ] Missing required field raises `ConfigError` with field name
  - [ ] `create_example_config("example.json")` creates valid example
  - [ ] `RepoConfig` dataclass has all 5 fields with defaults

  **QA Scenarios**:
  ```
  Scenario: Load valid JSON config
    Tool: Bash
    Preconditions: Create temp config.json
    Steps:
      1. Run: python -c "from gh_downloader.config import load_config; cfg = load_config('test_configs/valid.json'); print(len(cfg.repos))"
      2. Assert: prints number of repos
    Expected Result: Config loaded and validated
    Evidence: .omo/evidence/task-3-json-config.txt

  Scenario: Invalid config missing required fields
    Tool: Bash
    Preconditions: Create invalid config
    Steps:
      1. Run: python -c "from gh_downloader.config import load_config, ConfigError; load_config('test_configs/missing.json')"
      2. Assert: ConfigError raised with message mentioning missing field
    Expected Result: Clear validation error
    Evidence: .omo/evidence/task-3-invalid-config.txt
  ```

  **Commit**: YES (group with Task 2, 4)
  - Message: `feat(config): add JSON/YAML config parser with validation`
  - Files: `gh_downloader/config.py`
  - Pre-commit: `python -c "from gh_downloader.config import load_config; print('Config module OK')"`

---

- [x] 4. Utility modules (utils.py) — path handling, encoding, signal handling, platform checks

  **What to do**:
  - `safe_filename(name: str)` → replace Windows-unsafe chars (`:`, `"`, `<`, `>`, `|`, `?`, `*`) with `_`
  - `ensure_dir(path: str)` → create directory if not exists
  - `build_output_path(base_dir, owner, repo, version, asset_name, flat=False)` → construct output file path
    - flat=False: `base_dir/owner/repo/version/asset_name`
    - flat=True: `base_dir/asset_name` (warning on name collision)
  - `check_disk_space(path, required_bytes)` → estimate available space
  - `setup_signal_handler()` → register Ctrl+C handler for graceful shutdown
  - `is_long_path(path)` → check if path exceeds Windows MAX_PATH (260)
  - `try_enable_long_paths()` → attempt `\\?\` prefix on Windows
  - `format_size(bytes)` → human-readable file size
  - `format_speed(bytes_per_sec)` → human-readable speed
  - `get_terminal_encoding()` → detect chcp, return encoding info
  - `parse_repo_string(s)` → parse "owner/repo" from various formats (URL, shorthand)

  **Must NOT do**:
  - Don't use ctypes or platform-specific DLL calls
  - Don't depend on third-party libraries

  **Recommended Agent Profile**:
  - **Category**: `quick` - Utility functions, no complex logic
  - **Skills**: none needed

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 3)
  - **Blocks**: Task 5 (downloader needs path utils)
  - **Blocked By**: None

  **References**:
  - `os` module: `os.path`, `os.name`
  - `signal` module: `signal.signal(signal.SIGINT, handler)`
  - `shutil` module: `shutil.disk_usage()`

  **Acceptance Criteria**:
  - [ ] `safe_filename("file:name|bad<>chars.txt")` returns `file_name_bad_chars.txt`
  - [ ] `build_output_path(...)` works correctly for both flat and nested modes
  - [ ] `setup_signal_handler()` registers SIGINT handler
  - [ ] `format_size(1024)` returns "1.00 KB"
  - [ ] `parse_repo_string("https://github.com/owner/repo")` returns ("owner", "repo")
  - [ ] `parse_repo_string("owner/repo")` returns ("owner", "repo")

  **QA Scenarios**:
  ```
  Scenario: Filename sanitization on Windows
    Tool: Bash
    Preconditions: None
    Steps:
      1. Run: python -c "from gh_downloader.utils import safe_filename; print(safe_filename('test:file|name<>?.exe'))"
      2. Assert: output is "test_file_name__.exe"
    Expected Result: All unsafe characters replaced
    Evidence: .omo/evidence/task-4-safe-filename.txt

  Scenario: Path building
    Tool: Bash
    Preconditions: None
    Steps:
      1. Run: python -c "from gh_downloader.utils import build_output_path; print(build_output_path('./dl', 'owner', 'repo', 'v1.0', 'file.exe', flat=False))"
      2. Assert: output contains "dl/owner/repo/v1.0/file.exe"
    Expected Result: Path matches expected structure
    Evidence: .omo/evidence/task-4-path-building.txt
  ```

  **Commit**: YES (group with Task 2, 3)
  - Message: `feat(utils): add path handling, encoding, and signal utilities`
  - Files: `gh_downloader/utils.py`
  - Pre-commit: `python -c "from gh_downloader.utils import *; print('Utils module OK')"`

---

- [x] 5. Download engine (downloader.py) — concurrent downloads, resume, retry, caching

  **What to do**:
  - Implement `DownloadManager` class coordinating all downloads
  - Key features:
    - **Concurrent downloads**: Use `concurrent.futures.ThreadPoolExecutor` (max_workers configurable, default 4)
    - **Glob matching**: Use `fnmatch.filter()` to match asset filenames against user patterns
    - **Smart cache**: Check if file exists with same size before downloading; skip if match
    - **Resume support**: Check for partial files (`.part` extension), use `Range` header to resume
    - **Retry logic**: 3 retries with exponential backoff (1s, 3s, 9s) on network errors
    - **Progress tracking**: Callback-based progress reporting (no rich dependency)
    - **Dry run**: Collect matched assets and print what would be downloaded without actually downloading
    - **Error aggregation**: Continue on per-asset errors, collect failures, report summary
  - Methods:
    - `download_release(repo, pattern, version, output_dir, flat, dry_run)` → download all matching assets
    - `download_asset(asset_info, dest_path, resume)` → single asset download with resume
    - `match_assets(assets, patterns)` → filter assets by fnmatch patterns
    - `check_cache(dest_path, expected_size)` → returns True if valid cache hit
  - Use `requests.get(url, stream=True)` for memory-efficient large file downloads
  - Write to `.part` temp file, rename on completion
  - Return `DownloadResult` dataclass: total, skipped, downloaded, failed, errors list

  **Must NOT do**:
  - Don't use asyncio/aiohttp (keep it simple with threads)
  - Don't implement file extraction
  - Don't use global state

  **Recommended Agent Profile**:
  - **Category**: `deep` — Most complex module, concurrency, error handling, edge cases
  - **Skills**: none needed

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on Task 2, 4)
  - **Parallel Group**: Sequential (after Wave 1)
  - **Blocks**: Tasks 9, 10
  - **Blocked By**: Tasks 2, 4

  **References**:
  - `concurrent.futures`: `ThreadPoolExecutor`, `as_completed()`
  - `fnmatch`: `fnmatch.filter(names, pattern)`
  - HTTP Range: RFC 7233, `headers={"Range": f"bytes={start}-"}`
  - requests streaming: `response.iter_content(chunk_size=8192)`

  **Acceptance Criteria**:
  - [ ] `match_assets([...assets...], ["*.exe", "*.msi"])` returns only matching assets
  - [ ] `download_release("stedolan/jq", "*.tar.gz", "latest", "./tmp")` downloads at least 1 file
  - [ ] Second run with same params skips already-downloaded files (cache hit)
  - [ ] Interrupted download resumes from where it left off (`.part` file test)
  - [ ] `--dry-run` lists files without downloading (no files created)
  - [ ] Concurrent downloads run in parallel (verify via timing)
  - [ ] Retry mechanism: simulate network error, verify 3 retries
  - [ ] `DownloadResult` accurately reports total/downloaded/skipped/failed counts

  **QA Scenarios**:
  ```
  Scenario: Download matching files from real repo
    Tool: Bash
    Preconditions: Package installed, test output dir created
    Steps:
      1. Run: mkdir -p ./test_dl
      2. Run: python -c "from gh_downloader.downloader import DownloadManager; dm = DownloadManager(); result = dm.download_release('stedolan/jq', '*.tar.gz', 'latest', './test_dl'); print(f'Downloaded: {result.downloaded}, Skipped: {result.skipped}')"
      3. Assert: downloaded >= 1 file, file exists in test_dl/stedolan/jq/<version>/
    Expected Result: File downloaded successfully to structured path
    Evidence: .omo/evidence/task-5-download-success.txt

  Scenario: Dry run mode (no files created)
    Tool: Bash
    Preconditions: Empty test_dl directory
    Steps:
      1. Run: python -c "from gh_downloader.downloader import DownloadManager; dm = DownloadManager(); result = dm.download_release('stedolan/jq', '*.tar.gz', 'latest', './test_dl', dry_run=True); print(f'Matched: {result.downloaded}')"
      2. Assert: downloaded count > 0
      3. Run: dir ./test_dl (or ls)
      4. Assert: directory is still empty (no actual download)
    Expected Result: Dry run shows what would download but creates nothing
    Evidence: .omo/evidence/task-5-dry-run.txt

  Scenario: Cache skip on second run
    Tool: Bash
    Preconditions: First run just completed (files exist)
    Steps:
      1. Run: python -c "from gh_downloader.downloader import DownloadManager; dm = DownloadManager(); result = dm.download_release('stedolan/jq', '*.tar.gz', 'latest', './test_dl'); print(f'Skipped: {result.skipped}')"
      2. Assert: skipped count > 0 (or skipped == matched count if all cached)
    Expected Result: Already-downloaded files are skipped
    Evidence: .omo/evidence/task-5-cache-skip.txt
  ```

  **Commit**: YES
  - Message: `feat(dl): implement concurrent download engine with resume, retry, and caching`
  - Files: `gh_downloader/downloader.py`
  - Pre-commit: `python -c "from gh_downloader.downloader import DownloadManager; print('Downloader module OK')"`

---

- [x] 6. CLI interface (cli.py) — argparse-based CLI with multiple subcommands

  **What to do**:
  - Implement CLI using `argparse` with subcommands
  - Subcommands:
    - `download`: Download assets from a single repo
      - Positional: `owner/repo`
      - `--pattern, -p` (required, repeatable for multiple patterns)
      - `--version, -v` (default: "latest")
      - `--output, -o` (default: "./downloads")
      - `--flat` (flag, flat directory structure)
      - `--dry-run` (flag, simulate only)
      - `--concurrent, -j` (int, max concurrent downloads, default: 4)
      - `--no-cache` (flag, force re-download)
    - `config`: Download from config file
      - Positional: config file path
      - `--output, -o` (override global output dir)
      - `--flat` (flag)
      - `--dry-run` (flag)
      - `--concurrent, -j` (int)
    - `init`: Generate example config file
      - Positional: output path (default: "gh-dl-config.json")
    - `list`: List assets in a release without downloading
      - Positional: `owner/repo`
      - `--version, -v` (default: "latest")
  - Global flags: `--help`, `--version`
  - Parse repo string from URL format too (`https://github.com/owner/repo`)
  - Proper exit codes: 0 success, 1 partial failure, 2 fatal error

  **Must NOT do**:
  - Don't use click/typer (stay with argparse)
  - Don't print stack traces to users

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high` — Medium complexity CLI with subcommands
  - **Skills**: none needed

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 5, 7, 8)
  - **Blocks**: Task 9
  - **Blocked By**: Task 3

  **Acceptance Criteria**:
  - [ ] `gh-dl download --help` shows download subcommand help
  - [ ] `gh-dl config --help` shows config subcommand help
  - [ ] `gh-dl init example.json` creates example config
  - [ ] `gh-dl list stedolan/jq --version latest` prints assets
  - [ ] All exit codes correct (0/1/2)
  - [ ] Missing required args prints error with usage

  **QA Scenarios**:
  ```
  Scenario: Help display
    Tool: Bash
    Preconditions: Package installed
    Steps:
      1. Run: gh-dl --help
      2. Assert: shows subcommands: download, config, init, list
    Expected Result: Complete help with all subcommands
    Evidence: .omo/evidence/task-6-help.txt

  Scenario: List assets from repo
    Tool: Bash
    Preconditions: Internet access
    Steps:
      1. Run: gh-dl list stedolan/jq --version latest
      2. Assert: prints list of assets with names and sizes
      3. Assert: exit code 0
    Expected Result: Assets displayed without downloading
    Evidence: .omo/evidence/task-6-list-assets.txt

  Scenario: Error on invalid repo
    Tool: Bash
    Preconditions: None
    Steps:
      1. Run: gh-dl download nonexistent/owner --pattern "*.exe"
      2. Assert: exit code 2, error message contains "not found"
    Expected Result: Graceful error, no stack trace
    Evidence: .omo/evidence/task-6-error-handling.txt
  ```

  **Commit**: YES (group with Task 7)
  - Message: `feat(cli): implement argparse CLI with download/config/init/list subcommands`
  - Files: `gh_downloader/cli.py`
  - Pre-commit: `gh-dl --help && gh-dl list stedolan/jq --version latest`

---

- [x] 7. Interactive mode (interactive.py) — simple input-based TUI

  **What to do**:
  - Implement `run_interactive()` function with simple `input()`-based prompts
  - Flow:
    1. Welcome banner + brief instructions
    2. Loop: "Add a repo to download? (y/n)" → if n, proceed to download
    3. Prompt for repo: "Enter repo (owner/repo or URL):"
    4. Prompt for pattern: "File pattern (glob, e.g. *.exe):"
    5. Prompt for version: "Version (Enter for latest, or tag like v1.0.0):"
    6. Show summary of all repos queued
    7. Confirm: "Start download? (y/n)"
    8. Call `DownloadManager` with simple progress feedback
    9. Show download summary (total/downloaded/skipped/failed)
    10. Ask: "Download more? (y/n)" → restart loop or exit
  - Show progress with simple text: `[repo] asset.ext: 45% (4.5MB/10MB)`
  - Handle Ctrl+C gracefully

  **Must NOT do**:
  - Don't use rich/tqdm (keep dependency-free)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high` — User interaction flow
  - **Skills**: none needed

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 5, 6, 8)
  - **Blocks**: Task 9
  - **Blocked By**: Task 3

  **Acceptance Criteria**:
  - [ ] `gh-dl` (no args) starts interactive mode
  - [ ] Can add multiple repos in one session
  - [ ] Enter key for version defaults to "latest"
  - [ ] Invalid repo input prompts again
  - [ ] Summary shown after all downloads
  - [ ] Ctrl+C exits gracefully

  **QA Scenarios**:
  ```
  Scenario: Interactive mode starts
    Tool: Bash
    Preconditions: Package installed
    Steps:
      1. Run: echo -e "n\n" | gh-dl 2>&1
      2. Assert: output contains welcome message
      3. Assert: exit code 0
    Expected Result: Interactive mode runs through exit
    Evidence: .omo/evidence/task-7-interactive-start.txt

  Scenario: Full interactive download flow
    Tool: Bash
    Preconditions: Internet access
    Steps:
      1. Run: echo -e "y\nstedolan/jq\n*.tar.gz\nlatest\ny\nn" | gh-dl --output ./test_dl_interactive 2>&1
      2. Assert: output contains download progress
      3. Assert: files exist in test_dl_interactive/
    Expected Result: Full interactive flow works end-to-end
    Evidence: .omo/evidence/task-7-interactive-download.txt
  ```

  **Commit**: YES (group with Task 6)
  - Message: `feat(interactive): implement simple input-based interactive download mode`
  - Files: `gh_downloader/interactive.py`
  - Pre-commit: `echo -e "n\n" | gh-dl`

---

- [x] 8. Test infrastructure (tests/ setup + conftest.py)

  **What to do**:
  - Set up `tests/` directory with:
    - `conftest.py`: Shared fixtures (mock API responses, temp dirs, sample configs)
    - `test_api.py`, `test_config.py`, `test_downloader.py`, `test_utils.py`, `test_cli.py`
  - Create fixtures:
    - `sample_release_response`: Mock GitHub API release JSON
    - `sample_assets_response`: Mock GitHub API assets JSON
    - `temp_output_dir`: Temporary directory for download tests
  - Configure pytest in `pyproject.toml`
  - Ensure respx mocking works for API endpoint tests

  **Must NOT do**:
  - Don't test against real GitHub API in unit tests

  **Recommended Agent Profile**:
  - **Category**: `quick` — Test configuration and boilerplate
  - **Skills**: none needed

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 5, 6, 7)
  - **Blocks**: Task 10
  - **Blocked By**: None

  **Acceptance Criteria**:
  - [ ] `pytest tests/` runs without errors
  - [ ] respx mock fixtures work correctly
  - [ ] Temp directory fixture creates/cleans up properly
  - [ ] pytest config in pyproject.toml recognized

  **QA Scenarios**:
  ```
  Scenario: Test infrastructure runs
    Tool: Bash
    Preconditions: pip install -e .[dev]
    Steps:
      1. Run: pytest tests/ -v 2>&1
      2. Assert: pytest discovers test files without import errors
    Expected Result: Test suite runs
    Evidence: .omo/evidence/task-8-test-infra.txt
  ```

  **Commit**: YES (group with Task 9)
  - Message: `test(infra): set up pytest infrastructure with respx mocking fixtures`
  - Files: `tests/*`, `pyproject.toml` (pytest config)
  - Pre-commit: `pytest tests/ -v`

---

## Wave 3 (Integration + Polish)

- [x] 9. **Main entry points** — Already completed in Wave 2. `__main__.py` delegates to `cli.run_cli()`.

- [x] 10. **Integration testing** — Write integration tests that hit the real GitHub API against small public repos (stedolan/jq). Test: download to real files, dry-run, cache re-check, error paths. Add edge case handling for empty releases, no matching patterns, network timeouts, rate limiting.

- [x] 11. **Windows hardening** — Verify: long paths (>260 chars) handled gracefully, terminal encoding (chcp 65001) works, Ctrl+C cleans up .part files, safe_filename handles all edge cases, disk space check works.

- [x] 12. **Documentation** — Create README.md with: project description, installation (pip install -e .), usage examples for all 3 modes (CLI, config, interactive), all flags reference, exit codes, GITHUB_TOKEN setup, example output.

---

## Final Verification Wave (MANDATORY)

> 4 review agents run in PARALLEL. ALL must APPROVE.

- [x] F1. **Plan Compliance Audit** — `oracle`
  ✅ APPROVED — Must Have [12/12] | Must NOT Have [7/7] | Tasks [12/12] | 3 bug fixes verified

- [x] F2. **Code Quality Review** — `unspecified-high`
  ✅ APPROVED — Import [PASS] | Tests [132/132 PASS] | Previous Issues [3/3 fixed]

- [x] F3. **Real Manual QA** — `unspecified-high`
  ✅ APPROVED — All critical scenarios pass. Error messages now printed to user. Remaining failures were GitHub API rate limit (60 req/h unauthenticated), not code bugs.

- [x] F4. **Scope Fidelity Check** — `deep`
  ✅ APPROVED — Tasks [12/12 compliant] | Contamination [CLEAN] | No scope creep

---

## Commit Strategy

- **1**: `feat(init): scaffold project with pyproject.toml and package skeleton`
- **2,3,4**: `feat(api): implement GitHub Releases API client` + `feat(config): add JSON/YAML config parser` + `feat(utils): add path handling and signal utilities`
- **5**: `feat(dl): implement concurrent download engine with resume, retry, and caching`
- **6,7**: `feat(cli): implement argparse CLI` + `feat(interactive): implement input-based interactive mode`
- **8,9**: `test(infra): set up pytest infrastructure` + `feat(main): wire up entry points`
- **10**: `test(core): add integration tests for download pipeline`
- **11**: `fix(windows): add Windows hardening for long paths and encoding`
- **12**: `docs(readme): add usage documentation and examples`

## Success Criteria

### Verification Commands
```bash
pip install -e .
gh-dl --help                    # Expected: shows all subcommands
gh-dl list stedolan/jq          # Expected: lists assets with names
gh-dl download stedolan/jq --pattern "*.tar.gz"  # Expected: downloads file
gh-dl init example.json         # Expected: creates example config
gh-dl config example.json       # Expected: downloads from config
pytest tests/ -v                # Expected: all tests pass
```

### Final Checklist
- [x] All "Must Have" items implemented and verified
- [x] All "Must NOT Have" items absent from codebase
- [x] All tests pass (`pytest tests/ -v` → 0 failures)
- [x] Installation works (`pip install -e .` → `gh-dl` available)
- [x] Three operation modes all functional
- [x] Concurrent downloads verified
- [x] Private repo support verified with GITHUB_TOKEN
- [x] Dry-run mode shows correct preview
- [x] Cache/skip mechanism works on repeated runs
