from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_runtime_delivery_artifacts_exist():
    assert (ROOT / "Dockerfile").is_file()
    assert (ROOT / ".dockerignore").is_file()
    assert (ROOT / "docker-compose.yml").is_file()
    assert (ROOT / "requirements-runtime.txt").is_file()
    assert (ROOT / ".github" / "workflows" / "publish-image.yml").is_file()


def test_windows_launcher_reconciles_runtime_dependencies_for_existing_venv():
    launcher = (ROOT / "start_bot.bat").read_text(encoding="utf-8")
    lines = launcher.splitlines()

    existing_venv_check = lines.index(
        'if not exist ".venv\\Scripts\\python.exe" goto :setup_python'
    )
    python_version_check = lines.index(
        '".venv\\Scripts\\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1'
    )
    dependency_jump = lines.index("goto :install_dependencies", python_version_check)

    assert dependency_jump > existing_venv_check
    assert dependency_jump == python_version_check + 3
    assert ":install_dependencies" in lines
    assert '".venv\\Scripts\\python.exe" -m pip install --disable-pip-version-check -r "requirements-runtime.txt"' in lines


def test_windows_launcher_enables_utf8_for_unicode_console_output():
    launcher = (ROOT / "start_bot.bat").read_text(encoding="utf-8")
    lines = launcher.splitlines()

    utf8_mode = lines.index('set "PYTHONUTF8=1"')
    bot_launch = lines.index('".venv\\Scripts\\python.exe" bot.py')

    assert utf8_mode < bot_launch


def test_windows_launcher_has_visible_console_and_prevents_duplicate_bot_processes():
    launcher = (ROOT / "start_bot.bat").read_text(encoding="utf-8")

    assert "title SCHOOL DISCORD MANAGER - BOT CONSOLE" in launcher
    assert "Get-CimInstance Win32_Process" in launcher
    assert "ExecutablePath -ieq $python" in launcher
    assert "Use stop_bot.bat to stop it before starting another copy." in launcher
    assert 'echo Press Ctrl+C here to stop it normally.' in launcher
    assert launcher.index("for /f") < launcher.index('".venv\\Scripts\\python.exe" -m pip install')


def test_windows_stop_launcher_only_targets_this_project_bot():
    launcher = (ROOT / "stop_bot.bat").read_text(encoding="utf-8")

    assert "Get-CimInstance Win32_Process" in launcher
    assert r".venv\Scripts\python.exe" in launcher
    assert r"bot\.py" in launcher
    assert "/PID $target.ProcessId" in launcher
    assert "Stop-Process -Id $target.ProcessId -Force" in launcher
    assert "/im python.exe" not in launcher.lower()


def test_runtime_requirements_exclude_test_only_packages():
    requirements = (ROOT / "requirements-runtime.txt").read_text(encoding="utf-8")
    assert "discord.py" in requirements
    assert "python-dotenv" in requirements
    assert "pytest" not in requirements
    assert "pytest-asyncio" not in requirements


def test_docker_runtime_uses_non_root_user_and_correct_entrypoint():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER app" in dockerfile
    assert 'CMD ["python", "bot.py"]' in dockerfile
    assert "COPY data ./data" not in dockerfile
    assert "RUN mkdir -p /app/data" in dockerfile


def test_dockerignore_excludes_runtime_state():
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "data/" in dockerignore


def test_compose_persists_runtime_data_and_uses_published_image():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "image: ghcr.io/hm-abdellah/school-discord-manager:latest" in compose
    assert "env_file:" in compose
    assert "school_manager_data:/app/data" in compose
    assert "restart: unless-stopped" in compose
