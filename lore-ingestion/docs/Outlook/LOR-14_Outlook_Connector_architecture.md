> **Historical (as of 2026-09-25)** — superseded by `infra/outlook/` (the `connectors/outlook/` REST design was not built; §7 Azure admin-consent steps are still referenced elsewhere). Kept for context; do not treat as current.

**LOR-14**

**Outlook Connector Architecture**

Outlook Mail & Microsoft Teams Integration

FastAPI-based REST Connector • Lore Ingestion Pipeline

Version 2.0 • May 2026 • Status: Complete Design

# 1. Microsoft 365 Ecosystem

The Lore connector targets two primary Microsoft 365 channels: Outlook
Mail and Microsoft Teams, both accessed via the Microsoft Graph API and
exposed through a FastAPI REST layer.

## 1.1 Platform Scope

  -------------------------------------------------------------------------
  **Component**         **Included**   **Reason**
  --------------------- -------------- ------------------------------------
  Outlook Mail          **✅ Yes**     Primary communication channel

  Microsoft Teams       **✅ Yes**     Primary communication channel

  Calendar              ❌ No          Out of scope (secondary)

  SharePoint            ❌ No          Out of scope
  -------------------------------------------------------------------------

## 1.2 Data Organisation

### Outlook Mail

> Mailbox (user@org.com)
>
> ├── Folders (Inbox, Sent Items, Custom Folders)
>
> └── Messages
>
> ├── Message ID • Conversation ID (threading)
>
> ├── From / To / Cc / Bcc
>
> ├── Subject • Body (HTML or text)
>
> └── Received DateTime

### Microsoft Teams

> Teams
>
> ├── Channels
>
> │ └── Messages (top-level posts)
>
> │ └── Replies (replyToId → top-level)
>
> └── Chats (1-on-1, group)
>
> └── Messages (flat list)

## 1.3 User Identity: Azure Active Directory

-   Object ID (GUID) --- unique, immutable identifier across all
    Microsoft services

-   User Principal Name (UPN) --- login format: user@domain.com

-   Primary Email --- usually identical to UPN

-   Display Name --- full name as shown in Teams / Outlook

> Person Registry note: Azure AD Object IDs are GUIDs, not emails. The
> FastAPI /mail/users endpoint resolves GUID → UPN → canonical email for
> knowledge graph construction.

**1.4 Personal vs Organisational Microsoft Accounts**

The Lore Outlook connector only supports organisational Azure AD
accounts. Personal Microsoft accounts (Outlook.com, Hotmail.com) are out
of scope --- they lack admin consent capability and cannot support bulk
org-wide access.

  ----------------------- ------------------------- -------------------------
  **Feature**             **Organisational (Azure   **Personal (MSA)**
                          AD)**                     

  Domain format           user@company.com          user@outlook.com /
                                                    hotmail.com / live.com

  Managed by              IT department / Azure AD  Individual user

  Admin consent           ✅ One-time org-wide      ❌ No org admin exists
                          grant                     

  Service account         ✅ No user login needed   ❌ User must log in
                                                    manually

  Bulk user access        ✅ All org users via one  ❌ Single user only
                          app                       

  Microsoft Teams         ✅ Full org Teams data    ❌ Not available

  Enterprise/compliance   ✅ SOC 2, audit trail     ❌ No audit trail

  Lore connector support  ✅ FULLY SUPPORTED        ❌ NOT SUPPORTED
  ----------------------- ------------------------- -------------------------

# 2. Microsoft Graph API

  -----------------------------------------------------------------------
  **Aspect**               **Value**
  ------------------------ ----------------------------------------------
  Base endpoint            https://graph.microsoft.com/v1.0

  Authentication           OAuth 2.0 client credentials (Azure AD)

  Permission model         Application permissions (admin consent
                           required)

  Rate limiting            Per-app throttling --- 429 with Retry-After
                           header

  Pagination               \@odata.nextLink pattern

  Incremental sync         Delta query tokens
  -----------------------------------------------------------------------

## 2.1 Authentication Flow

The connector uses the OAuth 2.0 client credentials grant --- no user
login required. The FastAPI service authenticates as the registered
Azure AD app with org-wide application permissions.

1.  Register app in Azure AD → obtain Client ID, Client Secret, Tenant
    ID

2.  Configure application permissions: Mail.Read,
    ChannelMessage.Read.All, Chat.Read.All, User.Read.All,
    ChannelMember.Read.All

3.  IT admin grants organisation-wide admin consent (see Section 7)

4.  FastAPI service calls POST /auth/token which calls:

> POST https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token
>
> { grant_type: client_credentials, client_id, client_secret,
>
> scope: https://graph.microsoft.com/.default }
>
> → access_token (1-hour TTL, auto-refreshed by auth service)

5.  All other endpoints use the cached token via Depends(get_token)

# 3. Graph API Endpoints Used

## 3.1 Outlook Mail

  ----------------------------------------------------------------------------------
  **Graph Endpoint**                               **Used By FastAPI Route**
  ------------------------------------------------ ---------------------------------
  GET /users                                       GET /mail/users

  GET /users/{id}/messages                         GET /mail/fetch

  GET /users/{id}/messages/delta                   GET /mail/delta

  GET                                              GET /mail/fetch (fallback)
  /users/{id}/messages?\$filter=receivedDateTime   
  ge {date}                                        

  GET /users/{id}/mailFolders                      GET /mail/users (folder metadata)
  ----------------------------------------------------------------------------------

## 3.2 Microsoft Teams

  -----------------------------------------------------------------------------------------
  **Graph Endpoint**                                          **Used By FastAPI Route**
  ----------------------------------------------------------- -----------------------------
  GET /groups?\$filter=resourceProvisioningOptions/Any(x:x eq GET /teams/list
  \'Team\')                                                   

  GET /teams/{id}/channels                                    GET /teams/channels

  GET /teams/{id}/channels/{channelId}/messages               GET /teams/messages

  GET                                                         GET /teams/replies
  /teams/{id}/channels/{channelId}/messages/{msgId}/replies   

  GET /teams/{id}/channels/{channelId}/messages/delta         GET /teams/delta

  GET /chats/{chatId}/messages                                GET /teams/messages (chat
                                                              mode)

  GET /chats/{chatId}/messages/delta                          GET /teams/delta (chat mode)
  -----------------------------------------------------------------------------------------

# 4. Delta Queries --- Incremental Sync

Delta queries are Microsoft\'s mechanism for efficient incremental
fetching. The FastAPI connector stores delta tokens per user/channel in
delta_tokens.json and uses them on every subsequent sync call.

**Why Delta Queries? --- Plain English**

Without delta queries every sync re-fetches all messages --- 1,000
emails even if only 5 are new. A delta token is a bookmark: after each
sync Microsoft gives you one; next run you pass it back and receive only
what changed. After the first full sync each run is \~100-200x faster.

  ---------------- ---------------------------- --------------------------------------
  **Aspect**       **First Sync (Week 1)**      **Next Sync (Week 2+)**

  API call         GET                          GET
                   /users/{id}/messages/delta   \.../messages/delta?\$deltatoken=abc

  Token parameter  None required                \$deltatoken=abc123

  Messages         ALL (\~1,000)                ONLY new/changed (\~5)
  returned                                      

  Time to complete \~10 seconds                 \~0.05 seconds

  API calls needed \~10 calls (pagination)      1 call

  New token        Yes --- save it              Yes --- update stored token
  returned                                      
  ---------------- ---------------------------- --------------------------------------

**Efficiency Comparison**

  ---------- ----------------------- -------------- ---------- ------------
  **Week**   **Approach**            **Messages     **Time**   **API
                                     Fetched**                 Calls**

  Week 1     Full sync               1,000          \~10 sec   10

  Week 2     Delta sync              5              \~0.05 sec 1

  Week 3     Delta sync              3              \~0.03 sec 1

  Week 4     Delta sync              8              \~0.08 sec 1
  ---------- ----------------------- -------------- ---------- ------------

## 4.1 How It Works

> FIRST CALL to GET /mail/delta?user_id=alice:
>
> → Calls GET /users/alice/messages/delta (no token)
>
> → Fetches ALL messages, saves deltaToken to delta_tokens.json
>
> → Returns: { messages: \[\...1000 items\], delta_token: \'abc123\',
> is_full_sync: true }
>
> NEXT CALL to GET /mail/delta?user_id=alice:
>
> → Loads deltaToken \'abc123\' from delta_tokens.json
>
> → Calls GET /users/alice/messages/delta?\$deltatoken=abc123
>
> → Returns: { messages: \[\...5 new items\], delta_token: \'xyz789\',
> is_full_sync: false }

## 4.2 Delta Token Storage (delta_tokens.json)

> {
>
> \"mail:alice@org.com\": {
>
> \"delta_token\":
> \"https://graph.microsoft.com/v1.0/users/{id}/messages/delta?\$deltatoken=abc123\",
>
> \"last_sync\": \"2026-05-22T10:00:00Z\",
>
> \"message_count\": 1000
>
> },
>
> \"teams:team1:channel1\": {
>
> \"delta_token\":
> \"https://graph.microsoft.com/v1.0/teams/\.../messages/delta?\$deltatoken=xyz789\",
>
> \"last_sync\": \"2026-05-22T10:05:00Z\",
>
> \"message_count\": 250
>
> }
>
> }
>
> DELETE /delta/reset wipes delta_tokens.json and forces a full re-sync
> on next call. Useful during development and for debugging stale
> tokens.

# 5. KnowledgeEvent Mapping

The POST /map/mail and POST /map/teams endpoints accept raw Graph API
JSON and return a KnowledgeEvent Pydantic model. The mapping table below
is the authoritative reference.

  ----------------------------------------------------------------------------------
  **Source**   **Graph API Field**           **KnowledgeEvent   **Notes**
                                             Field**            
  ------------ ----------------------------- ------------------ --------------------
  Mail         receivedDateTime              timestamp          ISO 8601 format

  Mail         from.emailAddress.address     sender_id          Sender email address

  Mail         body.content                  body               HTML or plain text

  Mail         conversationId                thread_id          Thread grouping

  Mail         subject                       title              Email subject line

  Mail         toRecipients\[\*\].address    actors\[\]         CC/BCC also included

  Mail         id                            source_id          Graph message ID

  Teams        createdDateTime               timestamp          ISO 8601 format

  Teams        from.user.userPrincipalName   sender_id          Sender UPN

  Teams        body.content                  body               ⚠️ HTML --- must
                                                                strip

  Teams        replyToId                     thread_id          Thread link (if
                                                                reply)

  Teams        id                            source_id          Graph message ID

  Teams        channelId + teamId            channel_ref        Channel context tag

  Teams        messageType                   tags\[\]           Filter system
                                                                messages
  ----------------------------------------------------------------------------------

## 5.1 KnowledgeEvent Pydantic Model (models/knowledge_event.py)

> from pydantic import BaseModel
>
> from datetime import datetime
>
> from typing import List, Optional
>
> class KnowledgeEvent(BaseModel):
>
> id: str \# sha256(\"source:\" + source_id)
>
> source: str \# \"outlook_mail\" \| \"ms_teams\"
>
> source_id: str \# Graph message ID
>
> event_type: str \# \"email\" \| \"team_message\"
>
> timestamp: datetime
>
> title: str \# Subject (mail) or first 100 chars (teams)
>
> body: str \# Plain text --- HTML stripped
>
> sender_id: str \# Sender email / UPN
>
> actors: List\[str\] \# All participants
>
> thread_id: Optional\[str\] \# conversationId / replyToId
>
> org: str
>
> channel_ref: Optional\[str\] \# \"TeamName#ChannelName\" (Teams only)
>
> tags: List\[str\]
>
> raw: dict \# Original Graph API payload

## 5.2 Mail Mapping (services/mail_service.py)

> def map_mail(msg: dict, org: str) -\> KnowledgeEvent:
>
> return KnowledgeEvent(
>
> id = sha256(f\'outlook_mail:{msg\[\"id\"\]}\'),
>
> source = \"outlook_mail\",
>
> source_id = msg\[\"id\"\],
>
> event_type = \"email\",
>
> timestamp = msg\[\"receivedDateTime\"\],
>
> title = msg\[\"subject\"\],
>
> body = msg\[\"body\"\]\[\"content\"\], \# may be HTML
>
> sender_id = msg\[\"from\"\]\[\"emailAddress\"\]\[\"address\"\],
>
> actors = \[msg\[\"from\"\]\[\"emailAddress\"\]\[\"address\"\],
>
> \*\[r\[\"emailAddress\"\]\[\"address\"\] for r in
> msg\[\"toRecipients\"\]\]\],
>
> thread_id = msg\[\"conversationId\"\],
>
> org = org,
>
> tags = \[f\'conversation:{msg\[\"conversationId\"\]}\'\],
>
> raw = msg
>
> )

## 5.3 Teams Mapping (services/teams_service.py)

> def map_teams(msg: dict, team_name: str, channel_name: str, org: str)
> -\> KnowledgeEvent:
>
> clean_body = clean_html(msg\[\"body\"\]\[\"content\"\]) \# Always
> strip HTML
>
> return KnowledgeEvent(
>
> id = sha256(f\'ms_teams:{msg\[\"id\"\]}\'),
>
> source = \"ms_teams\",
>
> source_id = msg\[\"id\"\],
>
> event_type = \"team_message\",
>
> timestamp = msg\[\"createdDateTime\"\],
>
> title = clean_body\[:100\],
>
> body = clean_body,
>
> sender_id = msg\[\"from\"\]\[\"user\"\]\[\"userPrincipalName\"\],
>
> actors = \[msg\[\"from\"\]\[\"user\"\]\[\"userPrincipalName\"\]\],
>
> thread_id = msg.get(\"replyToId\"),
>
> org = org,
>
> channel_ref = f\"{team_name}#{channel_name}\",
>
> tags =
> \[f\'team:{msg.get(\"channelIdentity\",{}).get(\"teamId\",\"\")}\'\],
>
> raw = msg
>
> )

## 5.4 Repo Integration \-- KnowledgeEvent Schema Mapping

The Outlook connector must produce KnowledgeEvent objects that are
field-for-field compatible with the canonical dataclass already in
schemas.py and used by connectors/gmail/normalizer.py. No downstream
pipeline stage (triple extractor, comm graph, validator) should require
any change. Place the new file at connectors/outlook/normalizer.py,
importing from the same common/ packages.

## 5.4.1 Outlook Mail ? KnowledgeEvent field mapping

Every field maps to the same KnowledgeEvent dataclass used by the Gmail
normalizer. Conventions (make_id, Actor.to_dict, ISO timestamp strings)
are identical.

  ---------------------------------------------------------------------------------------------------------------------------------------------------
  **KnowledgeEvent   **Outlook Graph API source**                         **Gmail equivalent**                         **Conversion / notes**
  field**                                                                                                              
  ------------------ ---------------------------------------------------- -------------------------------------------- ------------------------------
  id                 msg\[\"id\"\]                                        message.get(\"id\")                          make_id(\"outlook.message\",
                                                                                                                       msg\[\"id\"\])

  source             \"outlook_mail\"                                     \"gmail\"                                    Hardcoded literal

  event_type         EventType.EMAIL_MESSAGE                              EventType.EMAIL_MESSAGE                      Same enum from schemas.py \--
                                                                                                                       no change

  timestamp          msg\[\"receivedDateTime\"\]                          message.get(\"internalDate\") ? ms?ISO       Already ISO 8601 from Graph
                                                                                                                       API \-- store as-is

  title              msg.get(\"subject\",\"(no subject)\")                Subject header or \"(no subject)\"           Identical fallback convention

  body               msg\[\"body\"\]\[\"content\"\]                       \_decode_body() ? \_clean_email_body()       If contentType==\"html\" run
                                                                                                                       clean_html() then truncate to
                                                                                                                       4000 chars

  primary_actor      msg\[\"from\"\]\[\"emailAddress\"\]\[\"address\"\]   sender.id (from From header)                 \_actor_from_recipient() ?
                                                                                                                       Actor(id=\"email:addr\", ?,
                                                                                                                       source=\"outlook_mail\")

  actors             toRecipients + ccRecipients + bccRecipients          To / Cc / Bcc headers                        List of Actor.to_dict();
                                                                                                                       sender first, then all
                                                                                                                       recipients

  url                https://outlook.office.com/mail/{id}                 https://mail.google.com/?#{message_id}       Deep-link to message in
                                                                                                                       Outlook Web App

  source_id          msg\[\"id\"\]                                        message.get(\"id\",\"\")                     Raw Graph message ID (opaque
                                                                                                                       string)

  org                org argument                                         org argument                                 Passed in by caller \--
                                                                                                                       identical

  project            msg.get(\"subject\",\"\")                            subject (thread subject as project)          Same convention as Gmail

  tags               \[\"email\", \"internal\"\|\"external\"\]            \[\"email\",\"internal\"\|\"external\"\] +   internal/external: compare
                                                                          labelIds                                     employee_email domain vs
                                                                                                                       sender domain \-- same logic

  parent_id          msg.get(\"conversationId\")                          make_id(\"gmail.thread\", thread_id)         make_id(\"outlook.thread\",
                                                                                                                       conversationId) if present

  raw                full Graph msg dict                                  {message_id, thread_id, label_ids,           {conversation_id,
                                                                          list_unsubscribe}                            internet_message_id,
                                                                                                                       body_content_type, categories}
  ---------------------------------------------------------------------------------------------------------------------------------------------------

## 5.4.2 Microsoft Teams ? KnowledgeEvent field mapping

Teams messages produce KnowledgeEvent with
event_type=EventType.TEAM_MESSAGE. HTML bodies are always stripped
before storage. The channel context is carried in tags and raw.

  -----------------------------------------------------------------------------------------------------------------------------------------------
  **KnowledgeEvent   **Teams Graph API source**                             **Gmail equivalent**                  **Conversion / notes**
  field**                                                                                                         
  ------------------ ------------------------------------------------------ ------------------------------------- -------------------------------
  id                 msg\[\"id\"\]                                          message.get(\"id\")                   make_id(\"ms_teams.message\",
                                                                                                                  msg\[\"id\"\])

  source             \"ms_teams\"                                           \"gmail\"                             Hardcoded literal

  event_type         EventType.TEAM_MESSAGE                                 EventType.EMAIL_MESSAGE               New enum value \-- add to
                                                                                                                  schemas.py EventType

  timestamp          msg\[\"createdDateTime\"\]                             internalDate ? ISO                    Already ISO 8601 from Graph API

  title              clean_body\[:100\]                                     Subject header                        First 100 chars of cleaned body
                                                                                                                  \-- Teams has no subject

  body               msg\[\"body\"\]\[\"content\"\]                         \_decode_body()                       Always HTML \-- run
                                                                                                                  clean_html() then truncate to
                                                                                                                  4000 chars

  primary_actor      msg\[\"from\"\]\[\"user\"\]\[\"userPrincipalName\"\]   sender.id                             \_actor_from_upn() ?
                                                                                                                  Actor(id=\"email:upn\", ?,
                                                                                                                  source=\"ms_teams\")

  actors             from.user only (top-level msg)                         To/Cc/Bcc headers                     Replies add more actors via
                                                                                                                  related_ids thread walk

  url                https://teams.microsoft.com/?/{id}                     https://mail.google.com/?             Deep-link to Teams message

  source_id          msg\[\"id\"\]                                          message.get(\"id\",\"\")              Raw Graph message ID

  org                org argument                                           org argument                          Identical

  project            team_name + \"#\" + channel_name                       subject                               \"TeamName#ChannelName\" string

  tags               \[\"teams\", team_id\]                                 \[\"email\",\"internal\"\]            Always internal; add
                                                                                                                  messageType for filtering

  parent_id          msg.get(\"replyToId\")                                 make_id(\"gmail.thread\",thread_id)   make_id(\"ms_teams.thread\",
                                                                                                                  replyToId) if reply

  raw                full Graph msg dict                                    {message_id,thread_id,?}              {team_id, channel_id,
                                                                                                                  reply_to_id, message_type}
  -----------------------------------------------------------------------------------------------------------------------------------------------

## 5.4.3 connectors/outlook/normalizer.py \-- full implementation

Drop this file at connectors/outlook/normalizer.py. It imports from the
same common/ packages as the Gmail normalizer and returns the identical
KnowledgeEvent dataclass.

> \# connectors/outlook/normalizer.py
>
> \"\"\"Outlook Mail + Teams -\> KnowledgeEvent normalizer.
>
> Mirrors field conventions of connectors/gmail/normalizer.py.
>
> \"\"\"
>
> from \_\_future\_\_ import annotations
>
> import re
>
> from html.parser import HTMLParser
>
> from typing import List, Optional
>
> from common.hashing import make_id
>
> from schemas import KnowledgeEvent, Actor, EventType
>
> \_MAX_BODY = 4000
>
> \# \-- HTML stripper (replaces html_cleaner.py for standalone use)
> \-\-\-\-\-\-\-\-\--
>
> class \_Strip(HTMLParser):
>
> def \_\_init\_\_(self): super().\_\_init\_\_(); self.\_t: list\[str\]
> = \[\]
>
> def handle_data(self, d): self.\_t.append(d)
>
> def text(self) -\> str: return re.sub(r\'\\s+\', \' \', \'
> \'.join(self.\_t)).strip()
>
> def \_clean_html(html: str) -\> str:
>
> s = \_Strip(); s.feed(html); return s.text()
>
> \# \-- Actor helper
> \-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\--
>
> def \_actor(ea: dict, source: str) -\> Actor:
>
> addr = ea.get(\'address\', \'\').lower().strip()
>
> return Actor(id=f\'email:{addr}\', display_name=ea.get(\'name\',
> addr),
>
> email=addr, source=source)
>
> \# \-- Outlook Mail
> \-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\--
>
> def normalize_outlook_message(
>
> msg: dict, org: str, employee_email: str) -\> KnowledgeEvent:
>
> \"\"\"Graph API mail message dict -\> KnowledgeEvent (same shape as
> Gmail).\"\"\"
>
> SRC = \'outlook_mail\'
>
> raw_id = msg\[\'id\'\]
>
> conv_id = msg.get(\'conversationId\', \'\')
>
> subject = msg.get(\'subject\') or \'(no subject)\'
>
> body_obj = msg.get(\'body\', {})
>
> raw_body = body_obj.get(\'content\', \'\')
>
> if body_obj.get(\'contentType\', \'\').lower() == \'html\':
>
> raw_body = \_clean_html(raw_body)
>
> body = raw_body\[:\_MAX_BODY\]
>
> sender = \_actor(msg.get(\'from\', {}).get(\'emailAddress\', {}), SRC)
>
> recips = \[\_actor(r.get(\'emailAddress\', {}), SRC)
>
> for lst in (msg.get(\'toRecipients\') or \[\],
>
> msg.get(\'ccRecipients\') or \[\],
>
> msg.get(\'bccRecipients\') or \[\])
>
> for r in lst\]
>
> emp_dom = employee_email.split(\'@\')\[-1\].lower()
>
> snd_dom = sender.email.split(\'@\')\[-1\].lower() if sender.email else
> \'\'
>
> tag_dir = \'internal\' if snd_dom == emp_dom else \'external\'
>
> return KnowledgeEvent(
>
> id = make_id(\'outlook.message\', raw_id),
>
> source = SRC,
>
> event_type = EventType.EMAIL_MESSAGE,
>
> timestamp = msg.get(\'receivedDateTime\', \'\'),
>
> title = subject,
>
> body = body,
>
> primary_actor = sender.id,
>
> actors = \[a.to_dict() for a in \[sender\] + recips\],
>
> url = f\'https://outlook.office.com/mail/{raw_id}\',
>
> source_id = raw_id,
>
> org = org,
>
> project = subject,
>
> tags = \[\'email\', tag_dir\],
>
> parent_id = make_id(\'outlook.thread\', conv_id) if conv_id else None,
>
> raw = {\'conversation_id\': conv_id,
>
> \'internet_message_id\': msg.get(\'internetMessageId\'),
>
> \'body_content_type\': body_obj.get(\'contentType\'),
>
> \'categories\': msg.get(\'categories\', \[\])},
>
> )
>
> \# \-- Microsoft Teams
> \-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\--
>
> def normalize_teams_message(
>
> msg: dict, team_name: str, channel_name: str,
>
> org: str) -\> KnowledgeEvent:
>
> \"\"\"Graph API Teams message dict -\> KnowledgeEvent.\"\"\"
>
> SRC = \'ms_teams\'
>
> raw_id = msg\[\'id\'\]
>
> body_obj = msg.get(\'body\', {})
>
> clean = \_clean_html(body_obj.get(\'content\', \'\'))\[:\_MAX_BODY\]
>
> upn = (msg.get(\'from\') or {}).get(\'user\',
> {}).get(\'userPrincipalName\', \'\')
>
> sender = Actor(id=f\'email:{upn.lower()}\', display_name=upn,
>
> email=upn.lower(), source=SRC)
>
> reply_to = msg.get(\'replyToId\')
>
> team_id = (msg.get(\'channelIdentity\') or {}).get(\'teamId\', \'\')
>
> chan_id = (msg.get(\'channelIdentity\') or {}).get(\'channelId\',
> \'\')
>
> return KnowledgeEvent(
>
> id = make_id(\'ms_teams.message\', raw_id),
>
> source = SRC,
>
> event_type = EventType.TEAM_MESSAGE,
>
> timestamp = msg.get(\'createdDateTime\', \'\'),
>
> title = clean\[:100\],
>
> body = clean,
>
> primary_actor = sender.id,
>
> actors = \[sender.to_dict()\],
>
> url = f\'https://teams.microsoft.com/l/message/{raw_id}\',
>
> source_id = raw_id,
>
> org = org,
>
> project = f\'{team_name}#{channel_name}\',
>
> tags = \[\'teams\', f\'team:{team_id}\'\],
>
> parent_id = make_id(\'ms_teams.thread\', reply_to) if reply_to else
> None,
>
> raw = {\'team_id\': team_id, \'channel_id\': chan_id,
>
> \'reply_to_id\': reply_to,
>
> \'message_type\': msg.get(\'messageType\')},
>
> )

## 5.4.4 Update routers/mapper.py to use the new normalizer

Replace the existing map_mail / map_teams service calls in
routers/mapper.py with calls to the new normalizer functions so that the
FastAPI endpoints return fully schema-compatible KnowledgeEvent objects.

> \# routers/mapper.py (updated)
>
> from fastapi import APIRouter, Query
>
> from connectors.outlook.normalizer import (
>
> normalize_outlook_message, normalize_teams_message)
>
> router = APIRouter()
>
> \@router.post(\'/mail\')
>
> async def map_mail_message(
>
> msg: dict,
>
> org: str = Query(default=\'default\'),
>
> employee_email: str = Query(default=\'\')):
>
> return normalize_outlook_message(msg, org, employee_email)
>
> \@router.post(\'/teams\')
>
> async def map_teams_message(
>
> msg: dict,
>
> team_name: str = Query(\...),
>
> channel_name: str = Query(\...),
>
> org: str = Query(default=\'default\')):
>
> return normalize_teams_message(msg, team_name, channel_name, org)

# 6. Filtering Strategy

The POST /mail/filter endpoint applies noise filters before messages are
passed to the mapping stage. Filters run at Stage 2 of the pipeline.

## 6.1 Mail Filters --- Skip if

-   Sender domain matches: noreply@\*, no-reply@\*, notifications@\*,
    mailer-daemon@\*

-   Subject contains: \'Out of Office\', \'Autoreply\', \'Delivery
    Report\', \'Undeliverable\'

-   Is non-delivery report / system message

-   Body length \< 5 characters after stripping whitespace

## 6.2 Teams Filters --- Skip if

-   messageType = \'systemEventMessage\' (user joined / left channel)

-   Sender is a bot: msg\[\'from\'\]\[\'application\'\] is not null

-   Body contains only emoji or \< 5 characters after cleaning

-   Timestamp falls between 02:00--06:00 local (likely automated
    notification)

# 7. Admin Consent Flow (Critical Enterprise Blocker)

> ⚠️ Every customer organisation must complete this flow exactly once.
> After admin consent is granted, the FastAPI service operates fully
> autonomously --- no user login is ever required again.

## Step 1 --- IT Admin: Register Azure AD App

6.  Go to https://portal.azure.com --- sign in as Global Administrator

7.  Navigate to: Azure Active Directory → App registrations → New
    registration

8.  Name: \'Lore Integration\' • Account type: Single tenant • Click
    Register

9.  Copy: Application (Client) ID and Directory (Tenant) ID from the
    Overview page

10. Go to Certificates & secrets → New client secret → set expiry → copy
    the Value immediately

11. Go to API permissions → Add a permission → Microsoft Graph →
    Application permissions

12. Add: Mail.Read, ChannelMessage.Read.All, Chat.Read.All,
    User.Read.All, ChannelMember.Read.All

    > **Note (LOR-65):** `ChannelMember.Read.All` was added in LOR-65 to
    > support `discover_teams_channel_members()` in `infra/outlook/discovery.py`.
    > This permission allows the connector to enumerate members of each Teams
    > channel, which is required to build per-user ACL scope maps
    > (`output/outlook/{org}_ms365_teams_scopes.json`). Without it the
    > `--stage discover` command will receive a 403 Forbidden from Graph
    > on the `/teams/{id}/channels/{channelId}/members` endpoint.

13. Click \'Grant admin consent for \[Organisation\]\' → Yes → all show
    ✅ Granted

## Step 2 --- Lore Team: Configure FastAPI Service

> \# .env
>
> TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
>
> CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
>
> CLIENT_SECRET=your-secret-value-here
>
> ORG_ID=contoso

## Step 3 --- Verify via FastAPI

> curl -X POST http://localhost:8000/auth/token
>
> \# Expected: { access_token: \'\...\', expires_in: 3600 }
>
> curl http://localhost:8000/health
>
> \# Expected: { status: \'ok\', token_valid: true, org: \'contoso\' }

## Step 4 --- Alternative: Admin Consent URL

> https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/adminconsent?
>
> client_id={client_id}&
>
> scope=Mail.Read%20ChannelMessage.Read.All%20Chat.Read.All%20User.Read.All%20ChannelMember.Read.All

Share this URL with the customer\'s IT admin if they cannot access Azure
Portal directly. They click it, review permissions, click Accept ---
one-time action.

# 8. FastAPI Connector Architecture

The connector is built as a standalone FastAPI service in a separate
repo. The folder structure below is the layout you create when setting
up that separate testing repo.

## 8.1 Folder Structure

> outlook-connector/
>
> ├── main.py \# FastAPI app entrypoint
>
> ├── .env \# TENANT_ID, CLIENT_ID, CLIENT_SECRET
>
> ├── requirements.txt
>
> ├── routers/
>
> │ ├── auth.py \# POST /auth/token
>
> │ ├── mail.py \# GET /mail/\* endpoints
>
> │ ├── teams.py \# GET /teams/\* endpoints
>
> │ ├── mapper.py \# POST /map/mail /map/teams
>
> │ └── system.py \# GET /health, DELETE /delta/reset
>
> ├── services/
>
> │ ├── auth_service.py \# get_access_token() --- Azure AD client
> credentials
>
> │ ├── mail_service.py \# fetch_messages(), delta_sync_user()
>
> │ ├── teams_service.py \# fetch_channel_messages(), get_replies()
>
> │ ├── filter_service.py \# apply_mail_filters(), apply_teams_filters()
>
> │ ├── html_cleaner.py \# clean_html() for Teams bodies
>
> │ └── delta_service.py \# load/save delta tokens (JSON file)
>
> ├── models/
>
> │ ├── knowledge_event.py \# KnowledgeEvent Pydantic model
>
> │ ├── graph_models.py \# GraphMessage, GraphUser Pydantic models
>
> │ └── requests.py \# Request body schemas
>
> ├── core/
>
> │ ├── config.py \# Settings via pydantic-settings (.env loader)
>
> │ ├── graph_client.py \# Async Graph API client (retry + pagination)
>
> │ └── exceptions.py \# Custom HTTP exceptions
>
> ├── tests/
>
> │ ├── conftest.py \# Pytest fixtures, TestClient setup
>
> │ ├── test_auth.py \# Auth endpoint tests
>
> │ ├── test_mail.py \# Mail fetch + delta tests
>
> │ ├── test_teams.py \# Teams fetch + delta tests
>
> │ ├── test_mapper.py \# Mapping unit tests
>
> │ ├── test_filter.py \# Filter logic tests
>
> │ └── fixtures/
>
> │ ├── mail_message.json \# Real API response saved for unit tests
>
> │ └── teams_message.json
>
> └── delta_tokens.json \# Runtime delta token store (gitignored)

# 9. Pipeline Integration

The FastAPI connector acts as Stage 1 (Fetch) of the Lore 6-stage
pipeline. Downstream stages call the connector\'s endpoints and receive
KnowledgeEvent JSON.

  ------------------------------------------------------------------------
  **Stage**    **FastAPI             **Notes**
               Interaction**         
  ------------ --------------------- -------------------------------------
  1 --- Fetch  GET /mail/delta/all + Delta-based; one call per
               GET /teams/delta      user/channel

  2 --- Filter POST /mail/filter     Returns filtered KnowledgeEvent list;
                                     Teams HTML cleaned here

  3 --- Person GET /mail/users       Resolves AAD GUID/UPN → canonical
  Registry                           email

  4 --- Comm   Downstream            Uses thread_id (conversationId /
  Graph                              replyToId) from KnowledgeEvent

  5 --- Triple Downstream (LLM)      Receives clean plain-text body ---
  Extractor                          same as Gmail pipeline

  6 ---        Downstream            HTML cleaning verified; delta dedup
  Validator                          check
  ------------------------------------------------------------------------

# 10. Outlook vs Gmail Connector Comparison

  ------------------------------------------------------------------------
  **Aspect**       **Gmail Connector**         **Outlook Connector**
  ---------------- --------------------------- ---------------------------
  Auth model       Domain-wide delegation      App registration + admin
                   (DWD)                       consent

  Service account  Google Cloud service        Azure AD app registration
                   account                     

  Incremental sync historyId-based             Delta query tokens

  Rate limits      Very high (\~100 req/sec)   Per-app throttling (429)

  HTML cleaning    Not required                Required (Teams messages)

  Thread model     Message-ID / In-Reply-To    conversationId (Mail) +
                   headers                     replyToId (Teams)

  API layer        FastAPI (same)              FastAPI (same) ✅

  KnowledgeEvent   Same                        Same ✅
  schema                                       

  6-stage pipeline Reference implementation    Same ✅ (filters adapted)

  Person registry  Google Directory            Azure AD /users endpoint

  Complexity       Medium                      High (admin consent
                                               blocker)
  ------------------------------------------------------------------------

## What Is Reusable from Gmail

-   KnowledgeEvent Pydantic model --- identical, no changes needed

-   FastAPI service pattern --- same structure, same dependency
    injection

-   6-stage pipeline architecture --- same flow, filters adapted per
    platform

-   Person registry pattern --- same concept, different directory source

-   Triple extractor LLM logic --- receives identical clean body format

## What Is Different

-   **Auth flow:** Azure AD client credentials vs Google service account
    DWD

-   **Delta token storage:** Per user + per channel (JSON) vs single
    historyId

-   **HTML body handling:** Teams messages are always HTML; mail may
    also be HTML

-   **Thread reconstruction:** conversationId (Mail) + replyToId (Teams)
    vs RFC headers

-   **Rate limit handling:** Retry-After header from 429 vs Google
    RateLimitExceeded

-   **Teams endpoint quirk:** /teams requires group filter --- see GET
    /teams/list implementation

# 11. Implementation Roadmap

  --------------------------------------------------------------------------
  **Phase**     **Timeline**   **Deliverables**
  ------------- -------------- ---------------------------------------------
  1 --- Azure   Week 1--2      Register app, validate API access, test admin
  Setup                        consent, confirm rate limits

  2 --- FastAPI Week 1         main.py + routers + /health endpoint, .env
  Skeleton                     config, graph_client.py

  3 --- Auth +  Week 2--3      POST /auth/token, GET /mail/users, GET
  Mail                         /mail/fetch, delta sync

  4 --- Teams   Week 4--5      GET /teams/list, /channels, /messages,
                               /replies, delta sync

  5 ---         Week 5--6      POST /map/mail, /map/teams, filter endpoints,
  Mapping +                    HTML cleaning
  Filter                       

  6 ---         Week 7--8      Person registry (AAD→email), comm graph
  Integration                  threads, full pipeline run

  7 --- Testing Week 9--12     E2E tests (10k+ msgs), throttle/retry,
                               customer docs, admin guide
  --------------------------------------------------------------------------

# 12. Success Criteria

  -----------------------------------------------------------------------
  **Success Criterion**                         **Target**
  --------------------------------------------- -------------------------
  All FastAPI endpoints return 200 with valid   100%
  KnowledgeEvent JSON                           

  /mail/delta/all handles 10k+ emails per org   10,000+ messages

  /teams/delta handles all channels             All channels covered

  Delta queries working (incremental syncs)     ≥ 90% incremental

  API error rate                                \< 5%

  Identity collision (AAD → person mapping)     \< 1%

  Thread reconstruction accuracy                conversationId +
                                                replyToId correct

  Admin consent flow documented                 Customer-ready guide
  -----------------------------------------------------------------------

# Appendix --- Deliverables Checklist (LOR-14)

  -----------------------------------------------------------------------
  **Deliverable**                      **Status**    **Section**
  ------------------------------------ ------------- --------------------
  Architecture document --- Outlook    ✅ Complete   Sections 1--9
  Mail + Teams via Graph API                         

  connectors/outlook/ (FastAPI) ---    ✅ Complete   Section 8.1
  Azure auth + delta query strategy                  

  Mapping table: Graph API →           ✅ Complete   Section 5
  KnowledgeEvent (Mail + Teams)                      

  Admin consent flow documentation     ✅ Complete   Section 7

  Comparison: Outlook Mail vs Gmail    ✅ Complete   Section 10
  connector                                          
  -----------------------------------------------------------------------

--- End of Document ---
