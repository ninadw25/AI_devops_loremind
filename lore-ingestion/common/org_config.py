"""
common/org_config.py
────────────────────
Organization configuration for the Lore ingestion pipeline.

Each customer org gets a config file (JSON) that tells the pipeline:
  - What email domains belong to this org
  - Which mailboxes are functional/shared (overrides)
  - Any org-specific noise domains to add (learned over time)

The pipeline auto-discovers most things — this config captures
what can't be auto-detected or what the user wants to override.

Config file: output/{org}_config.json
"""

import json
from pathlib import Path
from typing import Optional

from common.logger import get_logger

log = get_logger("common.org_config")


DEFAULT_CONFIG = {
    "org": "",
    "primary_domain": "",
    # All domains that belong to this org (e.g. ["ezobooks.in", "ezobanks.com"])
    "org_domains": [],
    # Extra email local parts to treat as functional mailboxes
    # (beyond the auto-detected ones like hr@, support@, sales@)
    "extra_functional_mailboxes": [],
    # Org-specific noise domains discovered during analysis
    # These supplement the universal noise list
    "discovered_noise_domains": [],
    # Domains that should NEVER be filtered (even if they look like noise)
    # e.g. a partner company that sends high-volume but relevant emails
    "allowlisted_domains": [],
    # If non-empty, only these Jira project keys are ingested (whitelist).
    # Leave empty to ingest all projects discovered in the org.
    "jira_projects": [],
    # Manual display name overrides for Jira accounts with bad/username-like names.
    # Key: bare Jira accountId (without jira:U prefix), Value: canonical real name.
    # Override wins over whatever Jira returns. Remove entry to revert to Jira name.
    # Example: {"712020:ae04b378-5917-4953-8539-d8f7abc38566": "Parth Shah"}
    "jira_display_name_overrides": {},
}


def load_org_config(org: str, config_dir: Path) -> dict:
    """Load org config from disk, or return defaults."""
    config_path = config_dir / f"{org}_config.json"
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        log.info("Loaded org config from %s", config_path)
        return config

    log.info("No org config found at %s — using defaults", config_path)
    config = dict(DEFAULT_CONFIG)
    config["org"] = org
    return config


def save_org_config(config: dict, config_dir: Path):
    """Save org config to disk."""
    config_path = config_dir / f"{config['org']}_config.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    log.info("Saved org config → %s", config_path)


def init_org_config(
    org: str,
    primary_domain: str,
    config_dir: Path,
    extra_domains: Optional[list[str]] = None,
    extra_functional: Optional[list[str]] = None,
) -> dict:
    """
    Initialize org config for a new customer.

    Called once during first setup. Subsequent runs load from disk.
    """
    config = load_org_config(org, config_dir)

    config["org"] = org
    config["primary_domain"] = primary_domain

    all_domains = set(config.get("org_domains", []))
    all_domains.add(primary_domain)
    if extra_domains:
        all_domains.update(extra_domains)
    config["org_domains"] = sorted(all_domains)

    if extra_functional:
        existing = set(config.get("extra_functional_mailboxes", []))
        existing.update(extra_functional)
        config["extra_functional_mailboxes"] = sorted(existing)

    save_org_config(config, config_dir)
    return config
