"""
Mock verification script for infra/outlook/run.py (Phase 9).

This script uses unittest.mock to patch the fetcher and other dependencies,
allowing for isolated testing of the runner's orchestration logic without
making any actual network calls or writing to the filesystem.
"""
import unittest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, call, ANY
import argparse

# Add project root to sys.path to allow for package imports
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

# The module to test
from infra.outlook import run as outlook_run

# Mock data generators
def mock_user_generator(count=2):
    return [{"userPrincipalName": f"user{i}@test.com", "displayName": f"User {i}"} for i in range(1, count + 1)]

def mock_channel_generator(count=2):
    return [{"team_id": f"team{i}", "team_name": f"Team {i}", "channel_id": f"chan{i}", "channel_name": f"Channel {i}"} for i in range(1, count + 1)]

def mock_message_generator(prefix, count=3):
    for i in range(1, count + 1):
        yield {"id": f"{prefix}_msg{i}", "content": f"This is message {i} from {prefix}."}

class TestOutlookRunScript(unittest.TestCase):

    @patch('infra.outlook.run.setup_logging')
    @patch('infra.outlook.run.get_settings')
    @patch('infra.outlook.fetcher.get_access_token')
    @patch('infra.outlook.run._get_users')
    @patch('infra.outlook.fetcher.fetch_teams_channels')
    @patch('infra.outlook.fetcher.fetch_mail_for_user')
    @patch('infra.outlook.fetcher.fetch_channel_messages')
    @patch('infra.outlook.run.JSONLWriter')
    def test_fetch_stage(self, mock_jsonl_writer, mock_fetch_teams, mock_fetch_mail, mock_fetch_channels, mock_get_users, mock_get_token, mock_settings, mock_logging):
        """Verify the 'fetch' stage orchestration."""
        print("\n--- Verifying 'fetch' stage ---")
        # --- Setup Mocks ---
        mock_get_token.return_value = "dummy_token"
        mock_get_users.return_value = mock_user_generator(2)
        mock_fetch_channels.return_value = mock_channel_generator(2)
        mock_fetch_mail.side_effect = [
            list(mock_message_generator("user1_mail", 2)),
            list(mock_message_generator("user2_mail", 2))
        ]
        mock_fetch_teams.side_effect = [
            list(mock_message_generator("chan1_teams", 3)),
            list(mock_message_generator("chan2_teams", 3))
        ]
        
        # --- Run ---
        sys.argv = ["", "--org", "test-org", "--stage", "fetch"]
        outlook_run.main()

        # --- Assertions ---
        mock_get_users.assert_called_once()
        mock_fetch_channels.assert_called_once()
        self.assertEqual(mock_fetch_mail.call_count, 2)
        self.assertEqual(mock_fetch_teams.call_count, 2)
        
        # Check that writers were created for both mail and teams (positional calls)
        self.assertEqual(mock_jsonl_writer.call_count, 2)
        for c in mock_jsonl_writer.call_args_list:
            self.assertFalse(c.args[1])  # dry_run=False passed positionally

        # Check that data was written
        instance = mock_jsonl_writer.return_value
        self.assertEqual(instance.write.call_count, 10) # 2*2 mail + 2*3 teams
        print("[PASS] 'fetch' stage verified.")

    @patch('infra.outlook.run.setup_logging')
    @patch('infra.outlook.run.get_settings')
    @patch('infra.outlook.fetcher.get_access_token')
    @patch('infra.outlook.run._get_users')
    @patch('infra.outlook.fetcher.fetch_teams_channels')
    @patch('infra.outlook.fetcher.fetch_mail_for_user')
    @patch('infra.outlook.fetcher.fetch_channel_messages')
    @patch('infra.outlook.run.JSONLWriter')
    def test_fetch_stage_dry_run(self, mock_jsonl_writer, mock_fetch_teams, mock_fetch_mail, mock_fetch_channels, mock_get_users, mock_get_token, mock_settings, mock_logging):
        """Verify --dry-run prevents file writing."""
        print("\n--- Verifying 'fetch' stage with --dry-run ---")
        # --- Setup Mocks ---
        mock_get_token.return_value = "dummy_token"
        mock_get_users.return_value = mock_user_generator(1)
        mock_fetch_channels.return_value = mock_channel_generator(1)
        mock_fetch_mail.return_value = list(mock_message_generator("user1_mail"))
        mock_fetch_teams.return_value = list(mock_message_generator("chan1_teams"))

        # --- Run ---
        sys.argv = ["", "--org", "test-org", "--stage", "fetch", "--dry-run"]
        outlook_run.main()

        # --- Assertions ---
        # Discovery and fetch should still happen
        mock_get_users.assert_called_once()
        mock_fetch_channels.assert_called_once()
        mock_fetch_mail.assert_called_once()
        mock_fetch_teams.assert_called_once()
        
        # Crucially, JSONLWriter should be initialized in dry_run mode (positional calls)
        self.assertEqual(mock_jsonl_writer.call_count, 2)
        for c in mock_jsonl_writer.call_args_list:
            self.assertTrue(c.args[1])  # dry_run=True passed positionally
        print("[PASS] '--dry-run' verified.")

    @patch('infra.outlook.run.setup_logging')
    @patch('runners.outlook_smoke.run')
    def test_smoke_stage(self, mock_smoke_run, mock_logging):
        """Verify the 'smoke' stage uses a deferred import."""
        print("\n--- Verifying 'smoke' stage ---")
        # --- Run ---
        sys.argv = ["", "--org", "test-org", "--stage", "smoke"]
        outlook_run.main()

        # --- Assertions ---
        # Check that the smoke runner function was called
        self.assertTrue(mock_smoke_run.called)
        mock_smoke_run.assert_called_once()
        
        # Check that it was called with the correct args
        call_args = mock_smoke_run.call_args[0][0]
        self.assertEqual(call_args.org, "test-org")
        self.assertEqual(call_args.stage, "smoke")
        print("[PASS] 'smoke' stage verified.")

    @patch('infra.outlook.run.setup_logging')
    @patch('infra.outlook.run.get_settings')
    @patch('infra.outlook.fetcher.get_access_token')
    @patch('infra.outlook.run._get_users')
    @patch('infra.outlook.fetcher.fetch_teams_channels')
    @patch('infra.outlook.fetcher.fetch_mail_for_user')
    @patch('infra.outlook.fetcher.fetch_channel_messages')
    @patch('infra.outlook.run.JSONLWriter')
    def test_limit_arg(self, mock_jsonl_writer, mock_fetch_teams, mock_fetch_mail, mock_fetch_channels, mock_get_users, mock_get_token, mock_settings, mock_logging):
        """Verify the --limit argument stops fetching."""
        print("\n--- Verifying --limit argument ---")
        # --- Setup Mocks ---
        mock_get_token.return_value = "dummy_token"
        mock_get_users.return_value = mock_user_generator(5)
        mock_fetch_channels.return_value = mock_channel_generator(5)
        # Generators to simulate streaming
        mock_fetch_mail.side_effect = lambda **kwargs: (msg for msg in mock_message_generator(kwargs.get('user_email'), 10))
        mock_fetch_teams.side_effect = lambda **kwargs: (msg for msg in mock_message_generator(kwargs.get('channel_id'), 10))
        instance = mock_jsonl_writer.return_value
        
        # --- Run ---
        sys.argv = ["", "--org", "test-org", "--stage", "fetch", "--limit", "3"]
        outlook_run.main()
        
        # --- Assertions ---
        # The writer should have been called exactly 3 times
        self.assertEqual(instance.write.call_count, 3)
        print("[PASS] '--limit' verified.")


if __name__ == '__main__':
    print("Running Phase 9 verification script for infra/outlook/run.py...")
    unittest.main(argv=['first-arg-is-ignored'], exit=False)
