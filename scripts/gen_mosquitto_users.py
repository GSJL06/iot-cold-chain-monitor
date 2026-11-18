"""Generate Mosquitto password file using docker run and mosquitto_passwd."""

import re
import shlex
import subprocess
import sys
from pathlib import Path


def parse_simple_env(env_path: Path) -> dict[str, str]:
    """Parse .env file with simple logic and no external dependencies."""
    if not env_path.is_file():
        raise FileNotFoundError(f"No se encontró el archivo de variables: {env_path}")
    env_vars: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        k = key.strip()
        v = val.strip()
        if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
            v = v[1:-1]
        env_vars[k] = v
    return env_vars


def extract_device_ids(seed_path: Path) -> list[str]:
    """Extract device_ids from seed SQL matching '(SIM-[A-Z0-9-]+)'."""
    if not seed_path.is_file():
        raise FileNotFoundError(f"No se encontró el archivo de seed SQL: {seed_path}")
    content = seed_path.read_text(encoding="utf-8")
    matches = re.findall(r"'(SIM-[A-Z0-9-]+)'", content)
    # Preserve order while deduplicating
    return list(dict.fromkeys(matches))


def generate_mosquitto_users() -> None:
    """Generate infra/mosquitto/passwd with ingestor and device credentials."""
    repo_root = Path(__file__).resolve().parent.parent
    env_path = repo_root / ".env"
    seed_path = repo_root / "infra" / "timescaledb" / "init" / "003_seed.sql"
    mosquitto_dir = (repo_root / "infra" / "mosquitto").resolve()

    env_vars = parse_simple_env(env_path)
    ingestor_pw = env_vars.get("MQTT_INGESTOR_PASSWORD")
    device_pw = env_vars.get("MQTT_DEVICE_PASSWORD")

    if not ingestor_pw or not device_pw:
        print(
            "Error: faltan MQTT_INGESTOR_PASSWORD o MQTT_DEVICE_PASSWORD en .env",
            file=sys.stderr,
        )
        sys.exit(1)

    device_ids = extract_device_ids(seed_path)
    users: list[tuple[str, str]] = [("ingestor", ingestor_pw)]
    for device_id in device_ids:
        users.append((device_id, device_pw))

    # Construct sh commands: first user uses -b -c (overwriting file), subsequent use -b
    commands: list[str] = []
    for i, (username, password) in enumerate(users):
        flag = "-b -c" if i == 0 else "-b"
        cmd_str = (
            f"mosquitto_passwd {flag} /work/passwd {shlex.quote(username)} {shlex.quote(password)}"
        )
        commands.append(cmd_str)

    sh_script = " && ".join(commands)

    docker_args = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{mosquitto_dir}:/work",
        "eclipse-mosquitto:2.0.23",
        "sh",
        "-c",
        sh_script,
    ]

    subprocess.run(docker_args, check=True)  # noqa: S603, S607
    print(f"Creados {len(users)} usuarios en infra/mosquitto/passwd")


if __name__ == "__main__":
    generate_mosquitto_users()
