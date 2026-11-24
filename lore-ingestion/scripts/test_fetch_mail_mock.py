"""Simple mock test to demonstrate fetch_mail_for_user first and second run flows.

This script monkeypatches `infra.outlook.fetcher.graph_get` to return
controlled responses including an `@odata.deltaLink` so the `DeltaStore`
updates can be observed without real network calls.
"""
import pathlib
import shutil
import json

from infra.outlook import fetcher


def make_page(values, delta_link=None):
    obj = {"value": values}
    if delta_link:
        obj["@odata.deltaLink"] = delta_link
    return obj


def mock_graph_get_first(endpoint, token, params=None, return_last_response=False):
    # Simulate two pages, last page contains deltaLink
    page1 = make_page([{"id": "m1"}, {"id": "m2"}])
    page2 = make_page([{"id": "m3"}], delta_link="https://graph.microsoft.com/v1.0/users/test@company.com/messages/delta?$deltatoken=FIRST_TOKEN")
    print(f"mock_graph_get_first called with endpoint={endpoint} params={params}")
    if return_last_response:
        # Return combined results and last page JSON
        return [*page1["value"], *page2["value"]], page2
    return [*page1["value"], *page2["value"]]


def mock_graph_get_incremental(endpoint, token, params=None, return_last_response=False):
    # Return a single-page incremental response with new deltaLink
    page = make_page([{"id": "m4"}], delta_link="https://graph.microsoft.com/v1.0/users/test@company.com/messages/delta?$deltatoken=NEXT_TOKEN")
    print(f"mock_graph_get_incremental called with endpoint={endpoint} params={params}")
    if return_last_response:
        return page["value"], page
    return page["value"]


def run_demo():
    out_dir = pathlib.Path("output") / "test_fetch"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    store_path = out_dir / "tokens.json"

    # Ensure DeltaStore uses our test path
    store = fetcher.DeltaStore("testorg", path=store_path)
    # Monkeypatch token acquisition to avoid real Azure AD calls
    fetcher.get_access_token = lambda settings=None: "DUMMY_TOKEN"
    # Monkeypatch graph_get for first run
    fetcher.graph_get = mock_graph_get_first

    print("--- First run (no token present) ---")
    for msg in fetcher.fetch_mail_for_user("testorg", "test@company.com", scanback_days=7, store_path=store_path):
        print("msg:", msg)

    store2 = fetcher.DeltaStore("testorg", path=store_path)
    print("Stored record after first run:", store2.get("mail:test@company.com"))

    # Monkeypatch graph_get for incremental run
    fetcher.graph_get = mock_graph_get_incremental

    print("--- Second run (existing token) ---")
    for msg in fetcher.fetch_mail_for_user("testorg", "test@company.com", scanback_days=7, store_path=store_path):
        print("msg:", msg)

    store3 = fetcher.DeltaStore("testorg", path=store_path)
    print("Stored record after second run:", store3.get("mail:test@company.com"))

    # Now test the case where the stored delta_token is a full deltaLink URL
    print("--- Third run (stored full deltaLink URL) ---")
    store_full = fetcher.DeltaStore("testorg", path=store_path)
    full_url = "https://graph.microsoft.com/v1.0/users/test@company.com/messages/delta?$deltatoken=FULL_TOKEN"
    store_full.set("mail:test@company.com", full_url, 0)
    store_full.save()

    # Monkeypatch graph_get to observe the endpoint used when replaying full URL
    def mock_graph_get_full(endpoint, token, params=None, return_last_response=False):
        print(f"mock_graph_get_full called with endpoint={endpoint} params={params}")
        page = make_page([{"id": "m5"}], delta_link="https://graph.microsoft.com/v1.0/users/test@company.com/messages/delta?$deltatoken=AFTER_FULL_TOKEN")
        if return_last_response:
            return page["value"], page
        return page["value"]

    fetcher.graph_get = mock_graph_get_full
    for msg in fetcher.fetch_mail_for_user("testorg", "test@company.com", scanback_days=7, store_path=store_path):
        print("msg:", msg)

    store4 = fetcher.DeltaStore("testorg", path=store_path)
    print("Stored record after third run:", store4.get("mail:test@company.com"))


if __name__ == "__main__":
    run_demo()
