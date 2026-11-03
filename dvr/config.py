"""Deployment settings read from the environment, fixed tunables, and logging setup.

User-facing settings (DVR address, schedule) are not here: they live in settings.json, which
is the only place they are read from. Environment values for them (DVR_HOST, TZ, AUTO_PRUNE_*)
are copied into settings.json once, on first start; see Storage.seed_from_env().
"""
import ipaddress
import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# ---------- tunables ----------
DVR_TIMEOUT = (3.05, 10)          # (connect, read) seconds for every DVR request
MAX_WORKERS = 6                   # bounded concurrency when loading each show's episodes
MAX_BODY_BYTES = 8 * 1024 * 1024  # refuse DVR responses bigger than this
MAX_DELETE_BATCH = 500            # most recordings one delete request may name
LIST_TTL = 10                     # seconds the recordings list is cached
RULES_TTL = 300                   # seconds the cloud recording rules are cached
MAX_REQUEST_BYTES = 1024 * 1024   # request bodies are small JSON; refuse anything bigger
HISTORY_MAX = 1000                # most deletions history keeps

# The DVR must be on one of these (RFC 1918 and IPv6 unique-local); DVR_ALLOWED_NETWORKS adds more.
DEFAULT_NETWORKS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")

# Environment variables copied into settings.json once, on first start.
SEEDED_ENV = ("DVR_HOST", "DVR_STORAGE_URL", "TZ", "AUTO_PRUNE_TIME", "AUTO_PRUNE_MAX")


def parse_networks(extra=""):
    nets = []
    for e in list(DEFAULT_NETWORKS) + [x.strip() for x in (extra or "").split(",") if x.strip()]:
        try:
            nets.append(ipaddress.ip_network(e, strict=False))
        except ValueError:
            log.warning("Ignoring invalid DVR_ALLOWED_NETWORKS entry: %s", e)
    return tuple(nets)


def configure_logging(level="INFO"):
    """Log to stderr (so `docker logs` shows it). Does nothing if logging is already set up."""
    lvl = getattr(logging, str(level).strip().upper(), None)
    logging.basicConfig(level=lvl if isinstance(lvl, int) else logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s")


@dataclass(frozen=True)
class Config:
    data_dir: str = "/data"
    app_user: str | None = None             # optional HTTP basic auth for the whole app
    app_pass: str | None = None
    allowed_networks: tuple = field(default_factory=parse_networks)
    device_auth: str | None = None          # override for the DVR's DeviceAuth (cloud recording rules)
    scheduler_enabled: bool = True          # DVR_SCHEDULER=0 turns the daily cleanup off entirely
    log_level: str = "INFO"
    env_defaults: dict = field(default_factory=dict)  # SEEDED_ENV values found in the environment

    @classmethod
    def from_env(cls, environ=None):
        env = os.environ if environ is None else environ

        def get(k):
            return (env.get(k) or "").strip() or None

        return cls(
            data_dir=get("DATA_DIR") or "/data",
            app_user=get("APP_USER"),
            app_pass=env.get("APP_PASS"),
            allowed_networks=parse_networks(env.get("DVR_ALLOWED_NETWORKS", "")),
            device_auth=get("DEVICE_AUTH"),
            scheduler_enabled=get("DVR_SCHEDULER") != "0",
            log_level=get("LOG_LEVEL") or "INFO",
            env_defaults={k: get(k) for k in SEEDED_ENV if get(k)},
        )
