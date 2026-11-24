from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from common.output_paths import JiraPaths, KnowledgePaths
from pipeline.extraction.grouping import load_person_registry


class TestJiraGroupingFallback(unittest.TestCase):
    def test_missing_email_principals_get_fallback_people(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d)
            kp = KnowledgePaths("testorg", data_dir=data_dir)
            kp.makedirs()
            jp = JiraPaths("testorg", data_dir=data_dir)
            jp.makedirs()

            registry = {
                "email_to_person_id": {
                    "alex@example.com": "person:alex@example.com",
                },
                "persons": [
                    {
                        "person_id": "person:alex@example.com",
                        "canonical_name": "Alex Email",
                        "primary_email": "alex@example.com",
                        "all_emails": ["alex@example.com"],
                        "all_names": ["Alex Email"],
                        "is_internal": True,
                        "is_functional": False,
                        "domains": ["example.com"],
                        "stats": {
                            "emails_sent": 1,
                            "emails_received": 0,
                            "total_activity": 1,
                            "threads_participated": 1,
                            "unique_contacts": 0,
                        },
                    }
                ],
            }
            kp.person_registry.write_text(json.dumps(registry), encoding="utf-8")

            artifact = {
                "scopes": [],
                "principals": [
                    {
                        "principal_id": "jira:Uacct-1",
                        "email": "",
                        "display_name": "Fallback Human",
                        "account_type": "atlassian",
                    },
                    {
                        "principal_id": "jira:Uacct-2",
                        "email": "alex@example.com",
                        "display_name": "Alex Jira",
                        "account_type": "atlassian",
                    },
                ],
                "scope_principals": [],
                "groups": [],
                "group_members": [],
            }
            jp.scope_principals.write_text(json.dumps(artifact), encoding="utf-8")

            email_to_person, persons_by_id = load_person_registry("testorg", data_dir)

            self.assertEqual(email_to_person["alex@example.com"], "person:alex@example.com")
            self.assertEqual(email_to_person["jira:Uacct-1"], "person:jira:Uacct-1")
            self.assertEqual(email_to_person["acct-1"], "person:jira:Uacct-1")
            self.assertEqual(email_to_person["jira:Uacct-2"], "person:alex@example.com")
            self.assertIn("person:jira:Uacct-1", persons_by_id)
            fallback = persons_by_id["person:jira:Uacct-1"]
            self.assertEqual(fallback["canonical_name"], "Fallback Human")
            self.assertEqual(fallback["primary_email"], "")
            self.assertEqual(fallback["source"], "jira_discovery_fallback")


if __name__ == "__main__":
    unittest.main()
