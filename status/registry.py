"""What there is to check, and where to find it.

Everything real — addresses, ssh target, port — is read from each project's
git-ignored `.deploy.env`, the same file its `./deploy` reads. Nothing in this
directory carries a value that could not be published, which is the rule the
rest of the repo follows and the reason the app table below is shape only.

An app whose `.deploy.env` is missing is reported as unconfigured rather than
skipped: a project that silently vanishes from a status page is exactly the
failure the page exists to prevent.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECTS = Path(os.environ.get("HOMELAB_PROJECTS", Path.home() / "projects"))


@dataclass(frozen=True)
class App:
    """One deployed application.

    `ip_var` names the `.deploy.env` key holding its LAN address rather than the
    address itself. `health` is the path its own health endpoint lives at —
    these genuinely differ per app (`/healthz`, `/status`, `/health`) and
    normalising them into one shape is `collect`'s job, not the deployer's.
    """

    name: str
    repo: str
    container: str
    #: `.deploy.env` key for the LAN address. None -> no reachable HTTP surface.
    ip_var: str | None = None
    port: int = 8080
    health: str = "/healthz"
    #: `.env` key holding the admin key, when the health endpoint needs one.
    auth_var: str | None = None
    #: Config file shipped to the NAS, compared against the repo's copy.
    shipped_config: str | None = "config.yaml"
    #: Remote app directory, for the shipped-config comparison.
    app_dir: str | None = None
    #: Extra JSON endpoints worth recording, as {label: path}.
    extra: dict[str, str] = field(default_factory=dict)
    #: Local venv, for tests and outdated packages. None -> not run locally.
    venv: str | None = ".venv"


APPS: tuple[App, ...] = (
    App(
        name="podcast-digest",
        repo="podcast-digest",
        container="podcast-agent",
        ip_var="APP_LAN_IP",
        port=8080,
        health="/healthz",
        auth_var="PODAGENT_ADMIN_API_KEY",
        app_dir="/share/Container/podcast-digest",
        extra={"status": "/api/v1/status", "runs": "/api/v1/runs/last"},
    ),
    App(
        name="video-digest",
        repo="video-digest",
        container="video-digest",
        ip_var="APP_LAN_IP",
        port=8090,
        health="/healthz",
        auth_var="VIDEODIGEST_ADMIN_API_KEY",
        app_dir="/share/Container/video-digest",
        extra={"metrics": "/metrics"},
    ),
    App(
        name="security-digest",
        repo="security-digest",
        container="security-digest-web",
        ip_var="APP_LAN_IP_SECURITY",
        port=8080,
        health="/status",
        app_dir="/share/Container/security-digest",
        shipped_config=None,
        # /status doubles as its run report: last_run, items_processed.
        extra={"run": "/status"},
    ),
    App(
        name="vault-ask",
        repo="vault-ask",
        container="vault-ask",
        ip_var="APP_LAN_IP",
        port=8080,
        health="/healthz",
        app_dir="/share/Container/vault-ask",
    ),
    App(
        name="taster",
        repo="taster",
        container="taster-worker",
        # The worker polls a cloud relay outbound and listens for nothing, so
        # there is no address to probe. Container state is the whole signal.
        ip_var=None,
        app_dir="/share/Container/taster",
        shipped_config=None,
        venv=None,
    ),
    App(
        name="family-calendar",
        repo="family_calendar",
        container="family-calendar",
        ip_var="APP_LAN_IP",
        # 8000, not the 8080 the others use. The proxy in front of it terminates
        # TLS on its own address; this is the app itself.
        port=8000,
        health="/healthz",
        app_dir="/share/Container/family-calendar",
        shipped_config=None,
    ),
    App(
        name="clippings-topics",
        repo="clippings-topics",
        container="clippings-topics",
        # A scheduled janitor: it wakes, works, and sleeps ~8h. No server.
        ip_var=None,
        app_dir="/share/Container/clippings-topics",
        shipped_config=None,
    ),
)


def read_env(path: Path) -> dict[str, str]:
    """`KEY=value` pairs from a .env-style file. Missing file -> empty."""
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def repo_path(app: App) -> Path:
    return PROJECTS / app.repo


def deploy_env(app: App) -> dict[str, str]:
    return read_env(repo_path(app) / ".deploy.env")


def app_env(app: App) -> dict[str, str]:
    return read_env(repo_path(app) / ".env")


def base_url(app: App) -> str | None:
    """`http://host:port`, or None when the app exposes nothing to probe."""
    if not app.ip_var:
        return None
    ip = deploy_env(app).get(app.ip_var)
    return f"http://{ip}:{app.port}" if ip else None


def ssh_target(app: App) -> tuple[str, str] | None:
    """(host, port) for ssh, from the project's own deploy config."""
    env = deploy_env(app)
    host, port = env.get("NAS_SSH"), env.get("NAS_SSH_PORT", "22")
    return (host, port) if host else None
