"""Mock fetcher for Jira that reads from seed_data.json instead of hitting real API."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass, field


@dataclass
class MockJiraFetcher:
    """Jira fetcher that reads from pre-generated mock data."""
    
    seed_path: Path
    base_url: str = "http://localhost:8091"
    admin_email: str = "mock@test.io"
    api_token: str = "mock-token"
    
    _data: dict = field(default_factory=dict)
    _projects: list[dict] = field(default_factory=list)
    _issues: list[dict] = field(default_factory=list)
    
    def __post_init__(self):
        """Load seed data from file."""
        with open(self.seed_path) as f:
            self._data = json.load(f)
        
        jira_data = self._data.get("jira", {})
        self._projects = jira_data.get("projects", [])
        self._issues = jira_data.get("issues", [])
    
    async def get(self, path: str, **kwargs) -> Any:
        """Mock GET request."""
        if path == "/rest/api/3/myself":
            return {"accountId": "sysadmin", "displayName": "System Admin", "emailAddress": "admin@techtest.io"}
        return None
    
    async def list_projects(self) -> list[dict]:
        """Return projects from seed data."""
        return [{"id": p["id"], "key": p["key"], "name": p["name"]} for p in self._projects]
    
    async def get_project_list(self) -> list[dict]:
        """Return projects from seed data."""
        return [{"id": p["id"], "key": p["key"], "name": p["name"]} for p in self._projects]
    
    async def fetch_issues_for_project(
        self,
        project_key: str,
        fields: list[str] | None = None,
        expand: list[str] | None = None,
    ) -> list[dict]:
        """Return issues for a project from seed data."""
        return [issue for issue in self._issues if issue.get("fields", {}).get("project", {}).get("key") == project_key]
    
    async def fetch_issue_changelog(self, issue_key: str) -> dict:
        """Return empty changelog for mock."""
        return {"histories": []}
    
    async def fetch_issues(
        self,
        jql: str,
        fields: list[str] | None = None,
        expand: list[str] | None = None,
        start_at: int = 0,
        max_results: int = 100,
        next_page_token: str | None = None,
    ) -> dict:
        """Return all issues from seed data (ignores JQL)."""
        # Return in paginated format like real Jira API
        return {
            "startAt": 0,
            "maxResults": len(self._issues),
            "total": len(self._issues),
            "issues": self._issues,
        }
    
    async def fetch_project_permission_scheme(self, project_key: str) -> dict:
        """Return empty permission scheme for mock."""
        return {"permissions": {}}
    
    async def fetch_security_schemes(self) -> list[dict]:
        """Return empty security schemes for mock."""
        return []
    
    def discover_fields(self, org: str) -> dict:
        """Return default field mapping for mock."""
        from common.org_config import load_org_config
        try:
            config = load_org_config(org)
            return config.get("jira_field_map", {})
        except Exception:
            return {}
    
    async def close(self):
        """No-op for mock."""
        pass
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, *args):
        pass


def create_mock_fetcher(seed_path: Path, base_url: str = "http://localhost:8091") -> MockJiraFetcher:
    """Factory function to create a mock fetcher."""
    return MockJiraFetcher(seed_path=seed_path, base_url=base_url)
