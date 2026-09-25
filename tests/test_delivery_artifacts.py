from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_runtime_delivery_artifacts_exist():
    assert (ROOT / "Dockerfile").is_file()
    assert (ROOT / ".dockerignore").is_file()
    assert (ROOT / "docker-compose.yml").is_file()
    assert (ROOT / "requirements-runtime.txt").is_file()
    assert (ROOT / ".github" / "workflows" / "publish-image.yml").is_file()


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
