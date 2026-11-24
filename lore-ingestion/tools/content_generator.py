"""Generate realistic Jira issue content using AWS Bedrock."""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import logging

from .bedrock_client import BedrockClient
from .config import JiraTestConfig

log = logging.getLogger(__name__)


@dataclass
class IssueContentRequest:
    """Request for generating issue content."""
    issue_type: str
    component: Optional[str] = None
    priority: str = "Medium"
    related_to: Optional[List[str]] = None  # Keys of related issues
    context: Optional[str] = None  # Additional context for the issue


@dataclass
class IssueContent:
    """Generated issue content."""
    summary: str
    description: str
    acceptance_criteria: Optional[List[str]] = None
    edge_cases: Optional[List[str]] = None
    labels: Optional[List[str]] = None


class IssueContentGenerator:
    """Generates realistic Jira issue content using AWS Bedrock."""
    
    def __init__(self, config: JiraTestConfig):
        self.config = config
        self.bedrock = BedrockClient(
            region=config.aws_region,
            model_id=config.bedrock_model_id,
            max_tokens=config.bedrock_max_tokens,
            temperature=config.bedrock_temperature,
        )
    
    def _build_prompt(self, request: IssueContentRequest) -> str:
        """Build prompt for issue generation."""
        issue_type = request.issue_type.lower()
        component = request.component or random.choice(self.config.components)
        priority = request.priority
        
        if issue_type == "bug":
            return f"""Generate a realistic bug report for a software tracking system.

Context:
- Component: {component}
- Priority: {priority}
- Issue Type: Bug

Return a JSON object with these fields:
{{
  "summary": "Brief bug title (max 100 chars)",
  "description": "Detailed bug description with: Steps to Reproduce, Expected Behavior, Actual Behavior",
  "labels": ["relevant", "labels"],
  "edge_cases": ["any edge case scenarios or special conditions"]
}}

Make it realistic - include technical details, error messages, or stack traces where appropriate."""

        elif issue_type == "story":
            return f"""Generate a realistic user story for a software tracking system.

Context:
- Component: {component}
- Priority: {priority}
- Issue Type: User Story

Return a JSON object with these fields:
{{
  "summary": "Story title in 'As a... I want... So that...' format",
  "description": "User story description with justification",
  "acceptance_criteria": ["Given X, When Y, Then Z", "Additional criteria"],
  "labels": ["features", "labels"]
}}

Make the acceptance criteria specific and testable."""

        elif issue_type == "epic":
            return f"""Generate an epic for organizing related stories.

Context:
- Component: {component}
- Issue Type: Epic

Return a JSON object with these fields:
{{
  "summary": "Epic title describing a major feature initiative",
  "description": "Epic overview describing the business value and high-level scope",
  "labels": ["epic", "initiative"],
  "goals": ["Goal 1", "Goal 2"]
}}

Make it comprehensive enough to span multiple sprints."""

        elif issue_type == "task":
            return f"""Generate a technical task for a software project.

Context:
- Component: {component}
- Priority: {priority}
- Issue Type: Task

Return a JSON object with these fields:
{{
  "summary": "Task title describing specific work item",
  "description": "Detailed task description with requirements",
  "labels": ["technical", "implementation"],
  "dependencies": ["Any dependencies on other work"]
}}

Make it actionable and specific."""

        elif issue_type == "sub-task":
            return f"""Generate a sub-task for an existing issue.

Context:
- Component: {component}
- Priority: {priority}
- Issue Type: Sub-task
- Parent Issue: {request.related_to[0] if request.related_to else 'N/A'}

Return a JSON object with these fields:
{{
  "summary": "Sub-task title",
  "description": "Specific work item description",
  "labels": ["subtask"]
}}

Keep it focused and small in scope."""

        else:
            return f"""Generate a general issue for a software tracking system.

Context:
- Component: {component}
- Priority: {priority}
- Issue Type: {issue_type}

Return a JSON object with these fields:
{{
  "summary": "Issue title",
  "description": "Issue description",
  "labels": ["general"]
}}"""

    def generate(self, request: IssueContentRequest) -> IssueContent:
        """Generate issue content using Bedrock."""
        prompt = self._build_prompt(request)
        
        result = self.bedrock.generate_json(
            prompt,
            system_prompt="You are a software developer creating realistic Jira issues. Generate believable, "
                         "professional content with technical details appropriate for a mid-size software team. "
                         "Return only valid JSON."
        )
        
        # Handle edge cases injection
        if random.random() < self.config.edge_case_probability:
            if "edge_cases" not in result:
                result["edge_cases"] = []
            result["edge_cases"].extend(self._get_random_edge_cases(request.issue_type))
        
        return IssueContent(
            summary=result.get("summary", f"{request.issue_type} {random.randint(1000, 9999)}"),
            description=result.get("description", "No description provided"),
            acceptance_criteria=result.get("acceptance_criteria"),
            edge_cases=result.get("edge_cases"),
            labels=result.get("labels"),
        )
    
    def _get_random_edge_cases(self, issue_type: str) -> List[str]:
        """Generate random edge case scenarios."""
        edge_cases_by_type = {
            "bug": [
                "Reproducible only on weekends",
                "Happens when daylight saving time changes",
                "User timezone affects behavior",
                "Only occurs with Unicode characters in input",
                "Browser-specific behavior (Safari only)",
                "Memory leak under high load",
                "Race condition in concurrent access",
            ],
            "story": [
                "Mobile-first requirement",
                "Accessibility compliance (WCAG 2.1 AA)",
                "Multi-region deployment requirement",
                "Offline functionality needed",
                "Multi-tenant isolation requirement",
            ],
            "task": [
                "Requires database migration",
                "Breaking API change",
                "Security audit required",
                "Performance benchmark needed",
            ],
            "sub-task": [
                "Cross-team coordination needed",
                "External API dependency",
            ],
            "epic": [
                "Strategic alignment with Q3 roadmap",
                "Customer SLA requirement",
            ],
        }
        
        applicable = edge_cases_by_type.get(issue_type.lower(), [])
        num_cases = random.randint(1, min(2, len(applicable))) if applicable else 0
        return random.sample(applicable, num_cases) if num_cases > 0 else []
    
    def generate_comment(
        self,
        issue_summary: str,
        issue_type: str,
        comment_type: str = "general",
        author_name: Optional[str] = None,
    ) -> str:
        """Generate a realistic comment for an issue."""
        
        comment_prompts = {
            "general": f"""Generate a brief, realistic comment for a Jira {issue_type} issue.

Issue: {issue_summary}

Return JSON: {{"comment": "A brief comment about this issue"}}
Keep it 1-3 sentences, professional tone.""",
            
            "status_update": f"""Generate a status update comment for a Jira issue.

Issue: {issue_summary}

Return JSON: {{"comment": "Status update with progress information"}}
Include specific work done or blockers encountered.""",
            
            "question": f"""Generate a clarifying question comment for a Jira {issue_type}.

Issue: {issue_summary}

Return JSON: {{"comment": "A question about the issue"}}
Ask about unclear requirements or implementation details.""",
            
            "review_feedback": f"""Generate code review feedback for a Jira issue.

Issue: {issue_summary}

Return JSON: {{"comment": "Review feedback"}}
Include specific technical feedback or suggestions.""",
            
            "resolution": f"""Generate a resolution comment for a closed Jira issue.

Issue: {issue_summary}

Return JSON: {{"comment": "Resolution summary"}}
Explain how the issue was resolved.""",
        }
        
        prompt = comment_prompts.get(comment_type, comment_prompts["general"])
        result = self.bedrock.generate_json(prompt)
        return result.get("comment", "Updated the issue.")
    
    def generate_bulk(
        self,
        requests: List[IssueContentRequest],
        batch_size: int = 5,
        delay_s: float = 1.0,
    ) -> List[IssueContent]:
        """Generate multiple issues in batches with rate limiting."""
        results = []
        
        for i in range(0, len(requests), batch_size):
            batch = requests[i:i + batch_size]
            batch_results = []
            
            for request in batch:
                try:
                    content = self.generate(request)
                    batch_results.append(content)
                except Exception as e:
                    log.warning(f"Failed to generate content: {e}")
                    # Fallback content
                    batch_results.append(IssueContent(
                        summary=f"{request.issue_type} - Generated {random.randint(1000, 9999)}",
                        description=f"Generated description for {request.issue_type}",
                    ))
            
            results.extend(batch_results)
            
            # Rate limiting between batches
            if i + batch_size < len(requests):
                import time
                time.sleep(delay_s)
        
        return results
