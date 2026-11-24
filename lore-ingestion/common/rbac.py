"""
common/rbac.py
──────────────
Department classification, access control, and sensitivity labeling.
"""

DEPARTMENT_KEYWORDS = {
    'hr': {'leave', 'salary', 'performance', 'lifecycle', 'compensation', 'pip',
           'onboarding', 'hiring', 'recruitment', 'attendance', 'hr_policy',
           'employee', 'offer_letter', 'termination', 'warning', 'probation'},
    'finance': {'payment', 'gst', 'finance', 'tax', 'invoice', 'accounting',
                'billing', 'refund', 'credit', 'bank', 'audit_financial',
                'revenue', 'expense', 'budget'},
    'sales': {'sales', 'lead', 'demo', 'target', 'commission', 'incentive',
              'customer', 'crm', 'pipeline', 'conversion', 'renewal'},
    'operations': {'operation', 'dispatch', 'logistics', 'support', 'queue',
                   'call_audit', 'reporting', 'daily_ops', 'compliance_ops'},
    'technology': {'technical', 'integration', 'api', 'webhook', 'dialer',
                   'software', 'system', 'server', 'database', 'code'},
    'legal': {'legal', 'compliance', 'regulatory', 'agreement', 'contract',
              'due_diligence', 'statutory', 'board_meeting', 'agm', 'director'},
    'product': {'product', 'design', 'prototype', 'manufacturing', 'hardware',
                'printer', 'machine', 'keypad'},
    'marketing': {'marketing', 'campaign', 'brand', 'social_media', 'content',
                  'seo', 'advertising'},
}

# Access hierarchy: who can see what departments
ACCESS_HIERARCHY = {
    'c_suite':    ['hr', 'finance', 'sales', 'operations', 'technology', 'legal', 'product', 'marketing'],
    'management': ['hr', 'finance', 'sales', 'operations', 'technology', 'legal', 'product', 'marketing'],
    'hr':         ['hr'],
    'finance':    ['finance'],
    'sales':      ['sales'],
    'operations': ['operations'],
    'technology': ['technology'],
    'legal':      ['legal'],
    'product':    ['product'],
    'marketing':  ['marketing'],
}

# HR, Finance, Legal content is confidential by default
CONFIDENTIAL_DEPARTMENTS = {'hr', 'finance', 'legal'}


def classify_departments(category_ids):
    """Determine which departments a set of category_ids belongs to.
    Returns a sorted list of department tags."""
    departments = set()
    for cat in (category_ids or []):
        cat_lower = cat.lower()
        for dept, keywords in DEPARTMENT_KEYWORDS.items():
            if any(kw in cat_lower for kw in keywords):
                departments.add(dept)
    if not departments:
        departments.add('general')
    return sorted(departments)


def compute_access_groups(departments):
    """Given department tags, compute who can access this node.
    Returns list of access group strings."""
    groups = set()
    for dept in departments:
        groups.add(dept)
    # Management and C-suite can always see everything
    groups.add('management')
    groups.add('c_suite')
    return sorted(groups)


def compute_sensitivity(departments):
    """Compute sensitivity level based on departments.
    Returns (score, label) tuple."""
    if any(d in CONFIDENTIAL_DEPARTMENTS for d in departments):
        return 0.7, 'confidential'
    return 0.3, 'internal'


ALLOWED_DEPARTMENTS = set(DEPARTMENT_KEYWORDS) | {'general'}
ALLOWED_SENSITIVITY = {'confidential', 'internal', 'public'}
