"""Jira ingestion support for lore-ingestion."""

from .discovery import JiraDiscovery, run_discovery
from .fetcher import JiraFetcher
from .normalizer import normalize_comment, normalize_issue, normalize_changelog

__all__ = [
    "JiraDiscovery",
    "JiraFetcher",
    "normalize_comment",
    "normalize_issue",
    "normalize_changelog",
    "run_discovery",
]
