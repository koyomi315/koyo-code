"""mcp.config 模块单测：两层合并 / ${VAR} 展开 / 字段校验 / 降级。"""

from pathlib import Path

from koyocode.mcp import Config, ServerConfig, load_config


def _write(path: Path, text: str) -> None:
    """把 text 写到 path，自动创建父目录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _patch_home(monkeypatch, home: Path) -> None:
    """把 Path.home() 指向临时 home 目录，隔离用户级配置。"""
    monkeypatch.setattr(Path, "home", lambda: home)


def test_both_files_missing(tmp_path: Path, monkeypatch) -> None:
    """两文件缺失 -> Config.servers 为空、无异常。"""
    _patch_home(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    project.mkdir()
    cfg = load_config(str(project))
    assert isinstance(cfg, Config)
    assert cfg.servers == {}


def test_user_level_only(tmp_path: Path, monkeypatch) -> None:
    """仅用户级有配置。"""
    home = tmp_path / "home"
    _write(
        home / ".koyocode" / "config.yaml",
        """
mcp_servers:
  alpha:
    type: stdio
    command: echo
""",
    )
    _patch_home(monkeypatch, home)
    project = tmp_path / "project"
    project.mkdir()
    cfg = load_config(str(project))
    assert set(cfg.servers) == {"alpha"}
    assert cfg.servers["alpha"].type == "stdio"
    assert cfg.servers["alpha"].command == "echo"


def test_project_level_only(tmp_path: Path, monkeypatch) -> None:
    """仅项目级有配置。"""
    _patch_home(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    _write(
        project / ".koyocode.yaml",
        """
mcp_servers:
  beta:
    type: http
    url: https://example.com/mcp
""",
    )
    cfg = load_config(str(project))
    assert set(cfg.servers) == {"beta"}
    assert cfg.servers["beta"].type == "http"
    assert cfg.servers["beta"].url == "https://example.com/mcp"


def test_merge_project_overrides_user(tmp_path: Path, monkeypatch) -> None:
    """同名 server 项目级完整覆盖用户级（不做字段级合并）。"""
    home = tmp_path / "home"
    _write(
        home / ".koyocode" / "config.yaml",
        """
mcp_servers:
  shared:
    type: stdio
    command: user-cmd
    args: ["--user"]
    env:
      K: userval
""",
    )
    _patch_home(monkeypatch, home)
    project = tmp_path / "project"
    _write(
        project / ".koyocode.yaml",
        """
mcp_servers:
  shared:
    type: http
    url: https://project.example.com/mcp
""",
    )
    cfg = load_config(str(project))
    assert set(cfg.servers) == {"shared"}
    s = cfg.servers["shared"]
    assert isinstance(s, ServerConfig)
    # 项目级完整覆盖：type/url 为项目级，command/args/env 不残留用户级
    assert s.type == "http"
    assert s.url == "https://project.example.com/mcp"
    assert s.command == ""
    assert s.args == []
    assert s.env == {}


def test_invalid_yaml_skipped(tmp_path: Path, monkeypatch, capsys) -> None:
    """格式非法 -> 跳过该层 + 告警，其它层正常加载。"""
    home = tmp_path / "home"
    _write(home / ".koyocode" / "config.yaml", 'name: "unclosed\n')  # 非法 YAML
    _patch_home(monkeypatch, home)
    project = tmp_path / "project"
    _write(
        project / ".koyocode.yaml",
        """
mcp_servers:
  good:
    type: stdio
    command: echo
""",
    )
    cfg = load_config(str(project))
    assert set(cfg.servers) == {"good"}  # 用户级非法被跳过
    err = capsys.readouterr().err
    assert "[mcp] warn:" in err


def test_var_expansion_defined(tmp_path: Path, monkeypatch) -> None:
    """${VAR} 已定义 -> 展开为环境值。"""
    monkeypatch.setenv("MY_TOKEN", "secret123")
    _patch_home(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    _write(
        project / ".koyocode.yaml",
        """
mcp_servers:
  svc:
    type: http
    url: https://example.com/mcp
    headers:
      Authorization: "Bearer ${MY_TOKEN}"
""",
    )
    cfg = load_config(str(project))
    assert cfg.servers["svc"].headers["Authorization"] == "Bearer secret123"


def test_var_expansion_undefined(tmp_path: Path, monkeypatch, capsys) -> None:
    """${VAR} 未定义 -> 空串 + 一次性告警。"""
    monkeypatch.delenv("NOPE_VAR", raising=False)
    _patch_home(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    _write(
        project / ".koyocode.yaml",
        """
mcp_servers:
  svc:
    type: http
    url: https://example.com/mcp
    headers:
      Authorization: "Bearer ${NOPE_VAR}"
""",
    )
    cfg = load_config(str(project))
    assert cfg.servers["svc"].headers["Authorization"] == "Bearer "
    err = capsys.readouterr().err
    assert "undefined env var ${NOPE_VAR}" in err


def test_var_undefined_warn_once(tmp_path: Path, monkeypatch, capsys) -> None:
    """同一 server 同一未定义变量多次引用只告警一次。"""
    monkeypatch.delenv("DUP_VAR", raising=False)
    _patch_home(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    _write(
        project / ".koyocode.yaml",
        """
mcp_servers:
  svc:
    type: http
    url: https://example.com/mcp
    headers:
      A: "${DUP_VAR}"
      B: "${DUP_VAR}"
""",
    )
    load_config(str(project))
    err = capsys.readouterr().err
    assert err.count("undefined env var ${DUP_VAR}") == 1


def test_var_not_expanded_in_command_args(tmp_path: Path, monkeypatch) -> None:
    """command / args 含 ${VAR} -> 不展开（保留字面量）。"""
    monkeypatch.setenv("X", "should-not-appear")
    _patch_home(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    _write(
        project / ".koyocode.yaml",
        """
mcp_servers:
  svc:
    type: stdio
    command: "${X}-cmd"
    args: ["--flag", "${X}"]
""",
    )
    cfg = load_config(str(project))
    s = cfg.servers["svc"]
    assert s.command == "${X}-cmd"  # 字面量保留
    assert s.args == ["--flag", "${X}"]


def test_invalid_type_skipped(tmp_path: Path, monkeypatch, capsys) -> None:
    """type 非法 -> 跳过该 server，其它不受影响。"""
    _patch_home(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    _write(
        project / ".koyocode.yaml",
        """
mcp_servers:
  bad:
    type: sse
    command: echo
  good:
    type: stdio
    command: echo
""",
    )
    cfg = load_config(str(project))
    assert set(cfg.servers) == {"good"}
    err = capsys.readouterr().err
    assert "skip server bad" in err


def test_missing_type_skipped(tmp_path: Path, monkeypatch) -> None:
    """type 缺失 -> 跳过。"""
    _patch_home(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    _write(
        project / ".koyocode.yaml",
        """
mcp_servers:
  notype:
    command: echo
""",
    )
    cfg = load_config(str(project))
    assert "notype" not in cfg.servers


def test_stdio_missing_command_skipped(tmp_path: Path, monkeypatch) -> None:
    """stdio 缺 command -> 跳过，其它 server 不受影响。"""
    _patch_home(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    _write(
        project / ".koyocode.yaml",
        """
mcp_servers:
  noarg:
    type: stdio
  good:
    type: http
    url: https://example.com/mcp
""",
    )
    cfg = load_config(str(project))
    assert set(cfg.servers) == {"good"}


def test_http_missing_url_skipped(tmp_path: Path, monkeypatch) -> None:
    """http 缺 url -> 跳过，其它 server 不受影响。"""
    _patch_home(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    _write(
        project / ".koyocode.yaml",
        """
mcp_servers:
  nourl:
    type: http
  good:
    type: stdio
    command: echo
""",
    )
    cfg = load_config(str(project))
    assert set(cfg.servers) == {"good"}


def test_load_config_never_raises(tmp_path: Path, monkeypatch) -> None:
    """load_config 永不抛出（root 不存在也不致错）。"""
    _patch_home(monkeypatch, tmp_path / "home")
    cfg = load_config(str(tmp_path / "nonexistent-project"))
    assert cfg.servers == {}


def test_example_yaml_parses(tmp_path: Path, monkeypatch) -> None:
    """读取 docs/ch07/mcp-servers.example.yaml，断言三个 server 都解析成功。"""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
    monkeypatch.setenv("EXAMPLE_TOKEN", "tok")
    _patch_home(monkeypatch, tmp_path / "home")  # 隔离用户级

    example = Path(__file__).parent.parent / "docs" / "ch07" / "mcp-servers.example.yaml"
    project = tmp_path / "project"
    project.mkdir()
    (project / ".koyocode.yaml").write_text(example.read_text(encoding="utf-8"), encoding="utf-8")

    cfg = load_config(str(project))
    assert set(cfg.servers) == {"github", "local-sqlite", "example-http"}
    assert cfg.servers["github"].type == "stdio"
    assert cfg.servers["github"].env["GITHUB_TOKEN"] == "ghp_xxx"
    assert cfg.servers["local-sqlite"].command == "python"
    assert cfg.servers["example-http"].type == "http"
    assert cfg.servers["example-http"].headers["Authorization"] == "Bearer tok"
