"""
domain/rbac.py
──────────────
Role-based access control rules. LEGACY — not on any query path.

The query-time ACL is the fail-closed scope gate in `domain/scopes.py`
(`scope_ids` OR / `scope_all_ids` AND, plus the audited `super_admin` flag).
Nothing calls `user_access_groups_from_role()`. The `access_groups` filters in
the legacy v1 retrieval (`domain/retrieval/{vector,expansion}.py`) are not on
the serving path either. The full-access default below does not govern live
queries.

What this module owns:
  - Department keyword taxonomy
  - Access hierarchy (who sees what department)
  - compute_access_groups / compute_sensitivity for ingestion
  - user_access_groups_from_role for query-time filtering
  - filter_document for vector-index RBAC
"""

import hashlib

# ── Deterministic hashing (used by ingestion, kept here for shared reference) ──

def short_hash(text: str, length: int = 12) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


# ── Department keyword taxonomy ─────────────────────────────────────────────

DEPARTMENT_KEYWORDS: dict[str, set[str]] = {
    "hr":         {"leave", "salary", "performance", "lifecycle", "compensation", "pip",
                   "onboarding", "hiring", "recruitment", "attendance", "hr_policy",
                   "employee", "offer_letter", "termination", "warning", "probation"},
    "finance":    {"payment", "gst", "finance", "tax", "invoice", "accounting",
                   "billing", "refund", "credit", "bank", "audit_financial",
                   "revenue", "expense", "budget"},
    "sales":      {"sales", "lead", "demo", "target", "commission", "incentive",
                   "customer", "crm", "pipeline", "conversion", "renewal"},
    "operations": {"operation", "dispatch", "logistics", "support", "queue",
                   "call_audit", "reporting", "daily_ops", "compliance_ops"},
    "technology": {"technical", "integration", "api", "webhook", "dialer",
                   "software", "system", "server", "database", "code"},
    "legal":      {"legal", "compliance", "regulatory", "agreement", "contract",
                   "due_diligence", "statutory", "board_meeting", "agm", "director"},
    "product":    {"product", "design", "prototype", "manufacturing", "hardware",
                   "printer", "machine", "keypad"},
    "marketing":  {"marketing", "campaign", "brand", "social_media", "content",
                   "seo", "advertising"},
}

ALLOWED_DEPARTMENTS: set[str] = set(DEPARTMENT_KEYWORDS) | {"general"}
ALLOWED_SENSITIVITY: set[str] = {"internal", "confidential", "public"}
CONFIDENTIAL_DEPARTMENTS: set[str] = {"hr", "finance", "legal"}

# Who can see which departments
ACCESS_HIERARCHY: dict[str, list[str]] = {
    "c_suite":    list(DEPARTMENT_KEYWORDS),
    "management": list(DEPARTMENT_KEYWORDS),
    "hr":         ["hr"],
    "finance":    ["finance"],
    "sales":      ["sales"],
    "operations": ["operations"],
    "technology": ["technology"],
    "legal":      ["legal"],
    "product":    ["product"],
    "marketing":  ["marketing"],
}

_FULL_ACCESS: list[str] = (
    list(ACCESS_HIERARCHY["management"]) + ["management", "c_suite", "general"]
)


def classify_departments(category_ids: list[str]) -> list[str]:
    """Map content category IDs to department tags."""
    departments: set[str] = set()
    for cat in (category_ids or []):
        cat_lower = cat.lower()
        for dept, keywords in DEPARTMENT_KEYWORDS.items():
            if any(kw in cat_lower for kw in keywords):
                departments.add(dept)
    return sorted(departments) if departments else ["general"]


def compute_access_groups(departments: list[str]) -> list[str]:
    """Compute RBAC access groups from department tags (used at ingestion time)."""
    groups: set[str] = set(departments)
    groups.add("management")
    groups.add("c_suite")
    return sorted(groups)


def compute_sensitivity(departments: list[str]) -> tuple[float, str]:
    """Return (score, label) for sensitivity classification."""
    if any(d in CONFIDENTIAL_DEPARTMENTS for d in departments):
        return 0.7, "confidential"
    return 0.3, "internal"


def filter_document(doc_metadata: dict, user_access_groups: list[str]) -> bool:
    """True if the user has access to this document."""
    doc_groups = set(doc_metadata.get("access_groups", []))
    if not doc_groups:
        return True  # no restriction → visible to all
    return bool(doc_groups & set(user_access_groups))


def user_access_groups_from_role(role: str) -> list[str]:
    """Convert a JWT role claim to the list of access groups for Neo4j queries.

    LEGACY: not called by any query path (see module docstring). Empty and
    unknown roles default to full management access.
    """
    role = (role or "").lower().strip()
    if not role or role in ("admin", "management", "c_suite"):
        return _FULL_ACCESS
    if role in ACCESS_HIERARCHY:
        return ACCESS_HIERARCHY[role] + ["management", "c_suite", "general"]
    # Unknown role → full access (legacy default, see docstring)
    return _FULL_ACCESS
