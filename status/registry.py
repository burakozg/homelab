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

import json
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
    #: `.deploy.env` key for the LAN address. None -> no reachable HTTP surface
    #: of its own (either a scheduled/polling worker, or reached only via
    #: Traefik — see `traefik_host`).
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
    #: The vault database this app is supposed to write to. Checked against what
    #: the running container actually has, because the two can silently differ.
    expect_vault_db: str | None = None
    #: The `Host:` header this app answers to behind Traefik, for apps with no
    #: `ip_var` address of their own any more (the 2026-09 central-login
    #: migration — see homelab/README.md "A second, private network...").
    #: `base_url` falls back to Traefik's own qnet address and the caller sends
    #: this as the Host header, since nothing on the Mac resolves `*.servers.zou`
    #: — that's dnsmasq's job, configured only on LAN devices. Only paths a
    #: Traefik router explicitly leaves unauthenticated (see the NAS's
    #: dynamic-config.yml, not tracked in any repo) are actually reachable this
    #: way; `health` must name one of those or the probe just gets a 302.
    traefik_host: str | None = None
    #: In-network address of the app itself, bypassing Traefik. Used only for
    #: `extra` endpoints that sit behind the login gate: the jobs container is on
    #: the same bridge as the app, and the gate is Traefik's, not the app's.
    internal_url: str | None = None
    #: Set when this app is a second running *instance* of another App's
    #: codebase (news-digest / security-digest: one repo, two deploys). Skips
    #: this entry in the repo-level slow checks (tests, packages, line counts)
    #: so they aren't computed and reported twice for one codebase — container
    #: identity, health and vault-destination checks still run independently.
    codebase_of: str | None = None


APPS: tuple[App, ...] = (
    App(
        name="podcast-digest",
        expect_vault_db="the_brain",
        repo="podcast-digest",
        container="podcast-agent",
        # No qnet address of its own since the 2026-09 central-login migration
        # (its .deploy.env no longer sets APP_LAN_IP at all) — reached only
        # through Traefik now. /healthz is the one unauthenticated exception
        # (added 2026-09-29 specifically so this probe would work again;
        # everything else, including /admin, stays behind the login gate).
        ip_var=None,
        traefik_host="podcast-digest.servers.zou",
        port=8080,
        health="/healthz",
        app_dir="/share/Container/podcast-digest",
        internal_url="http://podcast-agent:80",
        extra={"status": "/api/v1/status", "runs": "/api/v1/runs/last"},
    ),
    App(
        name="video-digest",
        expect_vault_db="the_brain",
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
        expect_vault_db="the_brain",
        repo="security-digest",
        container="security-digest-web",
        # Same migration as podcast-digest — APP_LAN_IP_SECURITY is gone from
        # .deploy.env. /status was already carved out unauthenticated in
        # Traefik from day one of the migration (it doubles as this app's own
        # run report), so no further Traefik change was needed here.
        ip_var=None,
        traefik_host="security-digest.servers.zou",
        port=8080,
        health="/status",
        app_dir="/share/Container/security-digest",
        shipped_config=None,
        extra={"run": "/status"},
    ),
    App(
        name="news-digest",
        # A second running instance of the security-digest codebase (one repo,
        # `./deploy --instance news`) — its own container, own topics/schedule,
        # own Traefik route. `codebase_of` skips it in the repo-level slow
        # checks (tests/packages/line-counts) so security-digest's numbers
        # aren't silently double-counted. Its vault destination isn't checked:
        # unlike the security instance, nothing here confirmed what database
        # it's supposed to write to, and a guessed expectation would be worse
        # than no check at all.
        codebase_of="security-digest",
        expect_vault_db=None,
        repo="security-digest",
        container="news-digest-web",
        ip_var=None,
        traefik_host="news-digest.servers.zou",
        port=8080,
        health="/status",
        app_dir="/share/Container/news-digest",
        shipped_config=None,
        extra={"run": "/status"},
        venv=None,
    ),
    App(
        name="vault-ask",
        expect_vault_db="the_brain",
        repo="vault-ask",
        container="vault-ask",
        # Same migration as podcast-digest/security-digest. Unlike them,
        # /healthz here was already reachable unauthenticated — only
        # `vaultAsk-admin` (PathPrefix /admin) carries the login-gate
        # middleware; the catch-all `vaultAsk-web` router (everything else,
        # including /healthz, /chat, /query) never did.
        ip_var=None,
        traefik_host="vault-ask.servers.zou",
        port=8080,
        health="/healthz",
        app_dir="/share/Container/vault-ask",
    ),
    App(
        name="taster",
        expect_vault_db="hobby",
        repo="taster",
        container="taster-worker",
        # The worker polls a cloud relay outbound and listens for nothing, so
        # container state is the signal for it. The health probe is the
        # sibling taster-admin container (same image, the data-maintenance UI),
        # reached only through Traefik like podcast-digest: /healthz is the one
        # unauthenticated path, everything else stays behind the login gate.
        ip_var=None,
        traefik_host="taster-admin.servers.zou",
        port=8088,
        health="/healthz",
        app_dir="/share/Container/taster",
        shipped_config=None,
        venv=None,
    ),
    App(
        name="family-calendar",
        expect_vault_db="hobby",
        repo="family_calendar",
        container="family-calendar",
        ip_var="APP_LAN_IP",
        # 8000, not the 8080 the others use. The proxy in front of it terminates
        # TLS on its own address; this is the app itself. Deliberately kept on
        # qnet (unlike its peers above) — see homelab/README.md's networking
        # section for why (the Inky Frame's fixed-IP firmware).
        port=8000,
        health="/healthz",
        app_dir="/share/Container/family-calendar",
        shipped_config=None,
    ),
    App(
        name="clippings-topics",
        expect_vault_db="the_brain",
        repo="clippings-topics",
        container="clippings-topics",
        # A scheduled janitor: it wakes, works, and sleeps ~8h. No server.
        ip_var=None,
        app_dir="/share/Container/clippings-topics",
        shipped_config=None,
    ),
    App(
        name="shortlist",
        # No vault integration — it's a standalone decision workspace, not
        # part of the Obsidian-vault fleet.
        expect_vault_db=None,
        repo="shortlist",
        container="shortlist",
        # Never had a qnet address — it joined homelab-internal from its first
        # deploy (see homelab/README.md). /healthz carved out unauthenticated
        # in Traefik on 2026-09-29, same reasoning as podcast-digest's.
        ip_var=None,
        traefik_host="shortlist.servers.zou",
        port=8080,
        health="/healthz",
        app_dir="/share/Container/shortlist",
    ),
    App(
        name="homelab-auth",
        # The login service itself — nothing to check against the vault, and
        # no LLM model to report.
        expect_vault_db=None,
        repo="homelab-auth",
        container="homelab-auth",
        # Kept on qnet deliberately: Traefik's forwardAuth calls it by this
        # address, and dnsmasq needs a stable target of its own to route
        # `auth.servers.zou` at. Its own /verify and /login are deliberately
        # NOT behind its own gate — see the NAS's dynamic-config.yml.
        ip_var="APP_LAN_IP",
        port=8098,
        health="/healthz",
        app_dir="/share/Container/homelab-auth",
        # No config.yaml — all configuration is environment variables.
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
    if _TARGETS is not None:
        # On the NAS there is no repo to read a .env from; the one secret a
        # probe needs arrives in the container's own environment.
        return {k: os.environ[k] for k in (app.auth_var,) if k and k in os.environ}
    return read_env(repo_path(app) / ".env")


def traefik_lan_ip() -> str | None:
    """Traefik's own qnet address, read from homelab-auth's `.deploy.env` —
    the only project that both needs and declares it (its forwardAuth
    middleware calls back into homelab-auth by this address' peer).

    Apps with no LAN address of their own any more (`traefik_host` set, no
    `ip_var`) are reached through Traefik instead — the caller must then send
    the app's own Host header itself, since nothing on the Mac resolves
    `*.servers.zou`; that's dnsmasq's job, and it's only configured on LAN
    devices, not this collector's host.
    """
    env = read_env(PROJECTS / "homelab-auth" / ".deploy.env")
    return env.get("TRAEFIK_LAN_IP")


#: Set by `load_targets` inside the homelab-jobs container, where none of the
#: Mac's per-project `.deploy.env` files exist. None -> resolve from them as usual.
_TARGETS: dict[str, dict[str, str | None]] | None = None

#: How the jobs container reaches Traefik: by name, over homelab-internal. The
#: Mac cannot do this and goes via Traefik's LAN address instead.
TRAEFIK_INTERNAL_URL = "http://traefik-1:80"


def load_targets(path: Path) -> None:
    """Switch `base_url`/`app_env` to a pre-resolved table (see `export_targets`)."""
    global _TARGETS
    _TARGETS = json.loads(path.read_text(encoding="utf-8"))


def export_targets() -> dict[str, dict[str, str | None]]:
    """Where the jobs container should probe each app — no secrets in it.

    Resolved here, on the Mac, because only the Mac has the `.deploy.env` files
    that say which apps still hold a qnet address. Everything else is reached
    through Traefik by container name. The one secret a probe needs (an admin
    key) is named, not carried: it travels in the container's own environment.
    """
    out: dict[str, dict[str, str | None]] = {}
    for app in APPS:
        ip = deploy_env(app).get(app.ip_var) if app.ip_var else None
        if ip:
            out[app.name] = {"url": f"http://{ip}:{app.port}", "auth_env": app.auth_var}
        elif app.traefik_host:
            out[app.name] = {"url": TRAEFIK_INTERNAL_URL, "auth_env": app.auth_var}
        if app.name in out and app.internal_url:
            out[app.name]["extra_url"] = app.internal_url
    return out


def extra_base_url(app: App) -> str | None:
    """Like `base_url`, but for `extra` endpoints that bypass the login gate."""
    if _TARGETS is not None:
        target = _TARGETS.get(app.name)
        return (target.get("extra_url") or target["url"]) if target else None
    return base_url(app)


def base_url(app: App) -> str | None:
    """`http://host:port`, or None when the app exposes nothing to probe.

    Prefers the app's own qnet address when it has one; falls back to
    Traefik's address for apps reached only that way (see `traefik_host` on
    `App`). Traefik listens on :80 for the `web` entrypoint regardless of any
    individual app's own port.
    """
    if _TARGETS is not None:
        target = _TARGETS.get(app.name)
        return target["url"] if target else None
    if app.ip_var:
        ip = deploy_env(app).get(app.ip_var)
        if ip:
            return f"http://{ip}:{app.port}"
    if app.traefik_host:
        ip = traefik_lan_ip()
        return f"http://{ip}:80" if ip else None
    return None


def ssh_target(app: App) -> tuple[str, str] | None:
    """(host, port) for ssh, from the project's own deploy config."""
    env = deploy_env(app)
    host, port = env.get("NAS_SSH"), env.get("NAS_SSH_PORT", "22")
    return (host, port) if host else None
