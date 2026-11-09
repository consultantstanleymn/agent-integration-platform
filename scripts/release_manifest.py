"""Write release/manifest.json: the facts that identify one release.

Fields: git_sha, image (name@sha256:digest), alembic_head, config_hash, prompt_version, prompt_sha256,
eval_set_sha256 (cases and testset), cassettes_sha256, created_at.
Usage: python scripts/release_manifest.py --image-digest localhost/agent-integration-platform@sha256:...
"""

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import SecretStr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from aip.config import Settings  # noqa: E402


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def config_hash(settings: Settings | None = None) -> str:
    """SHA-256 of the sorted non-secret settings. Fields of type SecretStr are left out by name."""
    settings = settings or Settings()
    secret_names = {n for n, f in type(settings).model_fields.items() if _is_secret(f.annotation)}
    values = {k: v for k, v in settings.model_dump(mode="json").items() if k not in secret_names}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def _is_secret(annotation: Any) -> bool:
    return annotation is SecretStr or SecretStr in getattr(annotation, "__args__", ())


def cassettes_hash(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.glob("*.json")):
        digest.update(f"{sha256_file(path)}  {path.name}\n".encode())
    return digest.hexdigest()


def alembic_head() -> str:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    return head or ""


def git_sha(allow_dirty: bool) -> str:
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout
    if dirty.strip() and not allow_dirty:
        raise SystemExit("working tree is dirty: commit first or pass --allow-dirty")
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def image_ref(digest_arg: str | None, local_image: str) -> str:
    if digest_arg:
        return digest_arg
    out = subprocess.run(
        ["podman", "image", "inspect", local_image, "--format", "{{.Digest}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    name = local_image.rsplit(":", 1)[0]
    return f"{name}@{out}"


def build(args: argparse.Namespace) -> dict[str, Any]:
    settings = Settings()
    prompt = ROOT / settings.prompts_dir / f"{settings.prompt_version}.txt"
    return {
        "git_sha": git_sha(args.allow_dirty),
        "image": image_ref(args.image_digest, args.local_image),
        "alembic_head": alembic_head(),
        "config_hash": config_hash(settings),
        "prompt_version": settings.prompt_version,
        "prompt_sha256": sha256_file(prompt),
        "eval_set_sha256": {
            "cases": sha256_file(ROOT / "evals" / "cases.yaml"),
            "testset": sha256_file(ROOT / "evals" / "testset.yaml"),
        },
        "cassettes_sha256": cassettes_hash(ROOT / "evals" / "cassettes"),
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-digest", default=None, help="name@sha256:digest")
    parser.add_argument("--local-image", default="localhost/agent-integration-platform:dev")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--out", default=str(ROOT / "release" / "manifest.json"))
    args = parser.parse_args(argv)
    manifest = build(args)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
