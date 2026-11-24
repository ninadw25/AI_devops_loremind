"""Configuration for Jira test data generator.

Uses AWS Bedrock for realistic LLM-generated content.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Literal, Optional


@dataclass
class JiraTestConfig:
    """Configuration for test data generation."""
    
    # AWS Bedrock settings
    aws_region: str = "us-east-1"
    bedrock_model_id: str = "anthropic.claude-3-sonnet-20240229-v1:0"
    bedrock_max_tokens: int = 4096
    bedrock_temperature: float = 0.7
    
    # Jira connection settings
    jira_base_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""
    
    # Generation settings
    num_issues: int = 50
    project_key: str = "TEST"
    project_name: str = "Test Project"
    
    # Timeline settings
    start_date: datetime = field(default_factory=lambda: datetime.now() - timedelta(days=90))
    end_date: datetime = field(default_factory=lambda: datetime.now())
    
    # User pool for realistic assignments
    users: List[dict] = field(default_factory=lambda: [
        {"accountId": "user-001", "displayName": "Alice Chen", "email": "alice@example.com"},
        {"accountId": "user-002", "displayName": "Bob Martinez", "email": "bob@example.com"},
        {"accountId": "user-003", "displayName": "Carol Johnson", "email": "carol@example.com"},
        {"accountId": "user-004", "displayName": "David Kim", "email": "david@example.com"},
        {"accountId": "user-005", "displayName": "Emma Wilson", "email": "emma@example.com"},
        {"accountId": "user-006", "displayName": "Frank Brown", "email": "frank@example.com"},
        {"accountId": "user-007", "displayName": "Grace Lee", "email": "grace@example.com"},
        {"accountId": "user-008", "displayName": "Henry Taylor", "email": "henry@example.com"},
    ])
    
    # Issue type distribution (weighted)
    issue_types: List[dict] = field(default_factory=lambda: [
        {"name": "Bug", "weight": 0.30},
        {"name": "Story", "weight": 0.35},
        {"name": "Task", "weight": 0.20},
        {"name": "Sub-task", "weight": 0.10},
        {"name": "Epic", "weight": 0.05},
    ])
    
    # Priority distribution
    priorities: List[dict] = field(default_factory=lambda: [
        {"name": "Highest", "weight": 0.05},
        {"name": "High", "weight": 0.15},
        {"name": "Medium", "weight": 0.50},
        {"name": "Low", "weight": 0.25},
        {"name": "Lowest", "weight": 0.05},
    ])
    
    # Status workflow
    statuses: List[dict] = field(default_factory=lambda: [
        {"name": "Open", "weight": 0.10},
        {"name": "In Progress", "weight": 0.20},
        {"name": "In Review", "weight": 0.15},
        {"name": "Done", "weight": 0.45},
        {"name": "Blocked", "weight": 0.05},
        {"name": "Closed", "weight": 0.05},
    ])
    
    # Component pool
    components: List[str] = field(default_factory=lambda: [
        "API", "Frontend", "Backend", "Database", "Authentication",
        "Reporting", "Integration", "Mobile", "Infrastructure", "Security"
    ])
    
    # Edge case probability settings
    edge_case_probability: float = 0.15  # 15% of issues have edge cases
