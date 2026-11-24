"""Mock verification harness for fetch_channel_messages().

Simulates 3 scenarios without requiring Azure creds or network access:
1. No delta token — full sync returns root messages and replies, stores token.
2. Existing delta token (short form) — incremental sync, updates token.
3. Stored full deltaLink URL — replayed directly, delta token updated.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from infra.outlook import fetcher  # noqa: E402


def make_root_messages(delta_link=None):
    """Mock root messages response with @odata.deltaLink."""
    obj = {
        "value": [
            {"id": "msg1", "from": {"emailAddress": {"address": "user1@org.com"}}, "subject": "Hello"},
            {"id": "msg2", "from": {"emailAddress": {"address": "user2@org.com"}}, "subject": "World"},
        ]
    }
    if delta_link:
        obj["@odata.deltaLink"] = delta_link
    return obj


def make_replies(parent_id):
    """Mock replies for a message."""
    return {
        "value": [
            {"id": f"reply1_of_{parent_id}", "from": {"emailAddress": {"address": "user3@org.com"}}, "subject": "Re: msg"},
            {"id": f"reply2_of_{parent_id}", "from": {"emailAddress": {"address": "user4@org.com"}}, "subject": "Re: Re: msg"},
        ]
    }


def test_scenario_1():
    """Scenario 1: No delta token — full sync."""
    print("=== Scenario 1: First sync (no delta token) ===")
    out_dir = pathlib.Path("output") / "test_teams_msg"
    import shutil
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    store_path = out_dir / "tokens.json"

    # Monkeypatch
    fetcher.get_access_token = lambda settings=None: "DUMMY_TOKEN"
    
    call_count = [0]
    
    def mock_graph_get_s1(endpoint, token, params=None, return_last_response=False):
        call_count[0] += 1
        if "messages/delta" in endpoint and "replies" not in endpoint:
            # Root messages endpoint
            print(f"  graph_get called: {endpoint} params={params}")
            resp = make_root_messages(
                delta_link="https://graph.microsoft.com/v1.0/teams/t1/channels/c1/messages/delta?$deltatoken=FIRST_TOKEN"
            )
            if return_last_response:
                return resp["value"], resp
            return resp["value"]
        elif "replies" in endpoint:
            # Replies endpoint
            print(f"  graph_get called: {endpoint}")
            return make_replies("msg1")["value"]
        raise AssertionError(f"Unexpected endpoint: {endpoint}")

    fetcher.graph_get = mock_graph_get_s1

    messages = list(fetcher.fetch_channel_messages("testorg", "t1", "Team1", "c1", "Channel1", store_path=store_path))
    print(f"  Fetched {len(messages)} messages")
    for msg in messages:
        print(f"    - {msg.get('id')}: team_id={msg.get('team_id')}, channel_id={msg.get('channel_id')}, replyToId={msg.get('replyToId', 'N/A')}")

    store = fetcher.DeltaStore("testorg", path=store_path)
    rec = store.get("teams:t1:c1")
    print(f"  Stored record: {rec}")
    assert rec is not None, "Delta token should be stored"
    assert rec["delta_token"] == "FIRST_TOKEN", "Token should match"
    print("  ✓ Scenario 1 passed\n")


def test_scenario_2():
    """Scenario 2: Existing delta token (short form) — incremental sync."""
    print("=== Scenario 2: Incremental sync (existing short delta token) ===")
    out_dir = pathlib.Path("output") / "test_teams_msg"
    store_path = out_dir / "tokens.json"

    # Pre-populate token
    store = fetcher.DeltaStore("testorg", path=store_path)
    store.set("teams:t1:c1", "FIRST_TOKEN", 4)
    store.save()

    fetcher.get_access_token = lambda settings=None: "DUMMY_TOKEN"
    
    def mock_graph_get_s2(endpoint, token, params=None, return_last_response=False):
        if "messages/delta" in endpoint and "replies" not in endpoint:
            print(f"  graph_get called: {endpoint} params={params}")
            # Verify that $deltatoken is in params
            assert params and "$deltatoken" in params, "Should use $deltatoken param"
            resp = make_root_messages(
                delta_link="https://graph.microsoft.com/v1.0/teams/t1/channels/c1/messages/delta?$deltatoken=SECOND_TOKEN"
            )
            if return_last_response:
                return resp["value"], resp
            return resp["value"]
        elif "replies" in endpoint:
            print(f"  graph_get called: {endpoint}")
            return make_replies("msg1")["value"]
        raise AssertionError(f"Unexpected endpoint: {endpoint}")

    fetcher.graph_get = mock_graph_get_s2

    messages = list(fetcher.fetch_channel_messages("testorg", "t1", "Team1", "c1", "Channel1", store_path=store_path))
    print(f"  Fetched {len(messages)} messages")
    for msg in messages:
        print(f"    - {msg.get('id')}: replyToId={msg.get('replyToId', 'N/A')}")

    store2 = fetcher.DeltaStore("testorg", path=store_path)
    rec2 = store2.get("teams:t1:c1")
    print(f"  Updated record: {rec2}")
    assert rec2["delta_token"] == "SECOND_TOKEN", "Token should be updated"
    print("  ✓ Scenario 2 passed\n")


def test_scenario_3():
    """Scenario 3: Stored full deltaLink URL — replay directly."""
    print("=== Scenario 3: Incremental sync (stored full deltaLink URL) ===")
    out_dir = pathlib.Path("output") / "test_teams_msg"
    store_path = out_dir / "tokens.json"

    # Pre-populate with full URL
    store = fetcher.DeltaStore("testorg", path=store_path)
    full_url = "https://graph.microsoft.com/v1.0/teams/t1/channels/c1/messages/delta?$deltatoken=FULL_URL_TOKEN"
    store.set("teams:t1:c1", full_url, 4)
    store.save()

    fetcher.get_access_token = lambda settings=None: "DUMMY_TOKEN"
    
    def mock_graph_get_s3(endpoint, token, params=None, return_last_response=False):
        if endpoint == full_url:
            # Replay full URL directly
            print(f"  graph_get called with full URL: {endpoint} params={params}")
            assert params is None, "Params should be None when replaying full URL"
            resp = make_root_messages(
                delta_link="https://graph.microsoft.com/v1.0/teams/t1/channels/c1/messages/delta?$deltatoken=AFTER_FULL_TOKEN"
            )
            if return_last_response:
                return resp["value"], resp
            return resp["value"]
        elif "replies" in endpoint:
            return make_replies("msg1")["value"]
        raise AssertionError(f"Unexpected endpoint: {endpoint}")

    fetcher.graph_get = mock_graph_get_s3

    messages = list(fetcher.fetch_channel_messages("testorg", "t1", "Team1", "c1", "Channel1", store_path=store_path))
    print(f"  Fetched {len(messages)} messages")

    store3 = fetcher.DeltaStore("testorg", path=store_path)
    rec3 = store3.get("teams:t1:c1")
    print(f"  Updated record: {rec3}")
    assert rec3["delta_token"] == "AFTER_FULL_TOKEN", "Token should be updated from full URL response"
    print("  ✓ Scenario 3 passed\n")


if __name__ == "__main__":
    test_scenario_1()
    test_scenario_2()
    test_scenario_3()
    print("✓ All 3 scenarios passed!")
