"""
infra/slack/socket_mode.py
Realtime Socket Mode ingestor adapted to repo layout.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional

from common.logger import get_logger
from common.schemas import KnowledgeEvent
from .fetcher import SlackFetcher, SLACK_BASE_URL
from .normalizer import build_knowledge_event, classify_thread, normalize_message

log = get_logger("slack.socket_mode")


class SlackSocketModeIngestor:
    def __init__(self, bot_token: str, app_token: str, org: str, base_url: str = SLACK_BASE_URL, output_file: Path | str | None = None, channel_types: Optional[str] = None, exclude_archived: bool = True, only_channels: Optional[list[str]] = None, include_private: bool = True, include_dms: bool = False) -> None:
        self.bot_token = bot_token
        self.app_token = app_token
        self.org = org
        self.exclude_archived = exclude_archived
        self.only_channels = only_channels
        self.include_private = include_private
        self.include_dms = include_dms
        self.channel_types = channel_types or self._default_channel_types()
        self.output_file = Path(output_file) if output_file else Path("output") / "raw" / f"{org}_slack_socket.jsonl"
        self.output_file.parent.mkdir(parents=True, exist_ok=True)

        self._fetcher = SlackFetcher(token=bot_token, org=org, base_url=base_url)
        self._channel_cache: dict[str, dict] = {}
        self._seen_ids: set[str] = set()
        self._stop_event = threading.Event()
        self._client = None

    def _default_channel_types(self) -> str:
        types = ["public_channel"]
        if self.include_private:
            types.append("private_channel")
        if self.include_dms:
            types.extend(["im", "mpim"])
        return ",".join(types)

    def prepare(self) -> None:
        self._fetcher.validate_auth()
        self._fetcher.load_user_cache()
        self.refresh_channel_cache()

    def refresh_channel_cache(self) -> list[dict]:
        channels = self._fetcher.list_channels(types=self.channel_types, exclude_archived=self.exclude_archived, only_channels=self.only_channels)
        self._channel_cache = {channel["id"]: channel for channel in channels}
        log.info("Socket Mode channel cache primed with %d channels", len(channels))
        return channels

    def _channel_for(self, channel_id: str, message: dict) -> dict:
        channel = self._channel_cache.get(channel_id)
        if channel:
            return channel
        return {"id": channel_id, "name": message.get("channel_name") or channel_id, "is_channel": True}

    def _thread_events(self, thread_messages: list[dict], channel: dict) -> list[KnowledgeEvent]:
        if not thread_messages:
            return []
        events: list[KnowledgeEvent] = []
        parent = thread_messages[0]
        replies = thread_messages[1:]
        parent_event = normalize_message(parent, channel, self.org, self._fetcher.user_cache, allowed_bot_ids=getattr(self._fetcher, "bot_allowlist", set()))
        if parent_event:
            node_type = classify_thread(parent.get("text", ""), replies)
            if node_type not in parent_event.tags:
                parent_event.tags.append(node_type)
            if parent_event.id not in self._seen_ids:
                events.append(parent_event)
                self._seen_ids.add(parent_event.id)
        for reply in replies:
            reply_event = normalize_message(reply, channel, self.org, self._fetcher.user_cache, allowed_bot_ids=getattr(self._fetcher, "bot_allowlist", set()))
            if reply_event and reply_event.id not in self._seen_ids:
                events.append(reply_event)
                self._seen_ids.add(reply_event.id)
        return events

    def process_message_event(self, message: dict) -> list[KnowledgeEvent]:
        if message.get("type") != "message":
            return []
        channel_id = message.get("channel")
        if not channel_id:
            return []
        channel = self._channel_for(channel_id, message)
        ts = message.get("ts", "")
        thread_ts = message.get("thread_ts", "")
        if ts and thread_ts and ts == thread_ts and message.get("reply_count", 0):
            thread_messages = self._fetcher.fetch_thread(channel_id, thread_ts)
            return self._thread_events(thread_messages, channel)
        event = normalize_message(message, channel, self.org, self._fetcher.user_cache, allowed_bot_ids=getattr(self._fetcher, "bot_allowlist", set()))
        if not event:
            return []
        if event.id in self._seen_ids:
            return []
        self._seen_ids.add(event.id)
        return [event]

    def _append_events(self, events: list[KnowledgeEvent]) -> None:
        if not events:
            return
        with self.output_file.open("a", encoding="utf-8") as handle:
            for event in events:
                record = {**event.to_dict(), "embedding_text": event.to_embedding_text()}
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _ack(self, client, request) -> None:
        envelope_id = getattr(request, "envelope_id", None)
        if not envelope_id:
            return
        try:
            from slack_sdk.socket_mode.response import SocketModeResponse
            client.send_socket_mode_response(SocketModeResponse(envelope_id=envelope_id))
        except Exception:
            client.send_socket_mode_response({"envelope_id": envelope_id})

    def _handle_socket_request(self, client, request) -> None:
        self._ack(client, request)
        if getattr(request, "type", "") != "events_api":
            return
        payload = getattr(request, "payload", {}) or {}
        event = payload.get("event", {}) or {}
        events = self.process_message_event(event)
        if events:
            self._append_events(events)
            log.info("Socket Mode wrote %d event(s) from %s", len(events), event.get("channel", "unknown"))

    def run(self) -> None:
        self.prepare()
        try:
            from slack_sdk import WebClient
            from slack_sdk.socket_mode import SocketModeClient
        except ImportError as exc:
            raise RuntimeError("Socket Mode requires slack-sdk. Install slack-sdk and set SLACK_APP_TOKEN.") from exc
        web_client = WebClient(token=self.bot_token)
        client = SocketModeClient(app_token=self.app_token, web_client=web_client)
        client.socket_mode_request_listeners.append(self._handle_socket_request)
        self._client = client
        log.info("Starting Slack Socket Mode listener for org=%s", self.org)
        client.connect()
        try:
            while not self._stop_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("Socket Mode listener interrupted by user")
        finally:
            try:
                client.disconnect()
            except Exception:
                pass

    def stop(self) -> None:
        self._stop_event.set()
