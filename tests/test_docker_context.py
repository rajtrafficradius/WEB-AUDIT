from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_docker_context_excludes_secrets_and_generated_client_outputs() -> None:
    patterns = {
        line.strip()
        for line in (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert ".env" in patterns
    assert ".env.*" in patterns
    assert "!.env.example" in patterns
    assert "*.sqlite3" in patterns
    assert ".venv" in patterns
    assert "exports" in patterns
    assert ".local-media" in patterns
    assert "*.patch" in patterns
