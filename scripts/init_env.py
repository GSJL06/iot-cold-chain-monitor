"""Script to initialize .env from .env.example with random hexadecimal secrets."""

import secrets
import sys
from pathlib import Path


def init_env() -> None:
    """Copy .env.example to .env replacing CHANGE_ME placeholders with secure secrets."""
    repo_root = Path(__file__).resolve().parent.parent
    env_example_path = repo_root / ".env.example"
    env_path = repo_root / ".env"

    if env_path.exists():
        print(f"Aviso: el archivo {env_path.name} ya existe. No se sobrescribe.", file=sys.stderr)
        sys.exit(1)

    if not env_example_path.exists():
        print(
            f"Error: no se encontró el archivo de plantilla {env_example_path.name}.",
            file=sys.stderr,
        )
        sys.exit(1)

    lines = env_example_path.read_text(encoding="utf-8").splitlines(keepends=True)
    out_lines: list[str] = []
    secret_count = 0

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            out_lines.append(line)
            continue

        key, sep, val = line.partition("=")
        key_strip = key.strip()
        val_content = val.rstrip("\r\n")
        ending = val[len(val_content) :]

        if key_strip.startswith("TELEGRAM_"):
            out_lines.append(line)
            continue

        val_strip = val_content.strip()
        if val_strip == "CHANGE_ME":
            secret = secrets.token_hex(16)
            secret_count += 1
            prefix_space = val_content[: len(val_content) - len(val_content.lstrip())]
            out_lines.append(f"{key}{sep}{prefix_space}{secret}{ending}")
        else:
            out_lines.append(line)

    env_path.write_text("".join(out_lines), encoding="utf-8")
    print(f"Generados {secret_count} secretos en {env_path.name}.")


if __name__ == "__main__":
    init_env()
