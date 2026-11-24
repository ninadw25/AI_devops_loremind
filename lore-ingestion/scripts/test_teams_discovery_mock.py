"""Mock verification harness for fetch_teams_channels().

This script monkeypatches `get_access_token()` and `graph_get()` so it can run
without Azure credentials or network access. It demonstrates:
- one Teams-enabled group returned from /groups
- two channels returned from /teams/{team_id}/channels
- flattened discovery output for later ACL work
"""
import pathlib
import sys


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from infra.outlook import fetcher  # noqa: E402


def mock_graph_get(endpoint, token, params=None, return_last_response=False):
    if endpoint == "/groups":
        return [
            {
                "id": "team1",
                "displayName": "Engineering",
            }
        ]

    if endpoint == "/teams/team1/channels":
        return [
            {
                "id": "channel1",
                "displayName": "backend",
            },
            {
                "id": "channel2",
                "displayName": "frontend",
            },
        ]

    raise AssertionError(f"Unexpected endpoint: {endpoint}")


def run_demo() -> None:
    fetcher.get_access_token = lambda settings=None: "DUMMY_TOKEN"
    fetcher.graph_get = mock_graph_get

    result = fetcher.fetch_teams_channels("testorg")
    print(result)


if __name__ == "__main__":
    run_demo()
