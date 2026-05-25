from __future__ import annotations

import unittest
from pathlib import Path


class DockerSupportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]

    def test_dockerfile_installs_runtime_dependencies_and_exposes_web_port(self) -> None:
        dockerfile = (self.repo_root / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("FROM python:3.12-slim", dockerfile)
        self.assertIn("apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core", dockerfile)
        self.assertIn("WORKDIR /app", dockerfile)
        self.assertIn("COPY requirements.txt .", dockerfile)
        self.assertIn("RUN pip install --no-cache-dir -r requirements.txt", dockerfile)
        self.assertIn("EXPOSE 8787", dockerfile)
        self.assertIn('CMD ["python", "app.py"]', dockerfile)

    def test_compose_file_publishes_port_and_persists_runtime_directories(self) -> None:
        compose = (self.repo_root / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn("build: .", compose)
        self.assertIn('- "8787:8787"', compose)
        self.assertIn("- ./data:/app/data", compose)
        self.assertIn("- ./output:/app/output", compose)
        self.assertIn("- ./sample_data:/app/sample_data", compose)
        self.assertIn("- RETROGUIDE_HOST=0.0.0.0", compose)
        self.assertIn("- RETROGUIDE_PORT=8787", compose)
        self.assertIn("host.docker.internal:host-gateway", compose)

    def test_dockerignore_excludes_local_git_and_runtime_state(self) -> None:
        dockerignore = (self.repo_root / ".dockerignore").read_text(encoding="utf-8")

        self.assertIn(".git", dockerignore)
        self.assertIn(".venv", dockerignore)
        self.assertIn("data/", dockerignore)
        self.assertIn("output/", dockerignore)


if __name__ == "__main__":
    unittest.main()
