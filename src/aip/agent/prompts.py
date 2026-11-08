from pathlib import Path


def load_prompt(prompts_dir: Path, version: str) -> str:
    """Prompts live in files so they are versioned in git and can be compared by the eval harness."""
    if not version.replace("_", "").isalnum():
        raise ValueError("bad prompt version")
    return (prompts_dir / f"{version}.txt").read_text().strip()
