import { useState } from 'react';
import {
  X,
  Copy,
  Check,
  ExternalLink,
  Mail,
  MessageSquare,
  Trello,
  Shield,
  Key,
  Info,
} from 'lucide-react';
import { Button } from '@/components/ui/button';

interface CopyableScopeProps {
  scope: string;
  isCopied: boolean;
  onCopy: (scope: string) => void;
}

function CopyableScope({ scope, isCopied, onCopy }: CopyableScopeProps) {
  return (
    <div className="flex items-center justify-between gap-3 p-2 bg-slate-50 border border-slate-200/60 rounded-lg text-xs font-mono text-slate-700 select-all">
      <span className="truncate">{scope}</span>
      <button
        type="button"
        onClick={() => onCopy(scope)}
        className="text-slate-400 hover:text-blue-600 transition-colors shrink-0"
        title="Copy scope"
      >
        {isCopied ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
      </button>
    </div>
  );
}

interface CredentialHelpModalProps {
  isOpen: boolean;
  onClose: () => void;
  defaultTab?: 'gmail' | 'slack' | 'jira';
}

export function CredentialHelpModal({ isOpen, onClose, defaultTab = 'gmail' }: CredentialHelpModalProps) {
  const [activeTab, setActiveTab] = useState<'gmail' | 'slack' | 'jira'>(defaultTab);
  const [copiedText, setCopiedText] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedText(text);
    setTimeout(() => setCopiedText(null), 2000);
  };

  return (
    <div className="fixed inset-0 bg-slate-900/60 backdrop-blur-sm flex items-center justify-center p-4 z-50 animate-in fade-in duration-200">
      <div className="bg-white rounded-2xl max-w-2xl w-full h-[90vh] md:h-[680px] flex flex-col shadow-2xl border border-slate-100 animate-in zoom-in-95 duration-200 overflow-hidden">
        {/* Modal Header */}
        <div className="px-6 py-4.5 border-b border-slate-100 flex items-center justify-between bg-slate-50/50">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-blue-50 flex items-center justify-center text-blue-600">
              <Info className="w-4 h-4" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-slate-900">Credential Setup Guide</h3>
              <p className="text-[11px] text-slate-400">Step-by-step configurations for organization ingestion</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-full hover:bg-slate-100 text-slate-400 hover:text-slate-600 transition-all"
          >
            <X className="w-4.5 h-4.5" />
          </button>
        </div>

        {/* Tab Selection */}
        <div className="px-6 border-b border-slate-100 flex gap-2 pt-3">
          <button
            onClick={() => setActiveTab('gmail')}
            className={`pb-3 text-xs font-bold uppercase tracking-wider border-b-2 px-1 transition-all flex items-center gap-1.5 ${
              activeTab === 'gmail'
                ? 'border-red-500 text-slate-800'
                : 'border-transparent text-slate-400 hover:text-slate-600'
            }`}
          >
            <Mail className="w-3.5 h-3.5" />
            Gmail (DWD)
          </button>
          <button
            onClick={() => setActiveTab('slack')}
            className={`pb-3 text-xs font-bold uppercase tracking-wider border-b-2 px-1 transition-all flex items-center gap-1.5 ${
              activeTab === 'slack'
                ? 'border-purple-500 text-slate-800'
                : 'border-transparent text-slate-400 hover:text-slate-600'
            }`}
          >
            <MessageSquare className="w-3.5 h-3.5" />
            Slack Bot
          </button>
          <button
            onClick={() => setActiveTab('jira')}
            className={`pb-3 text-xs font-bold uppercase tracking-wider border-b-2 px-1 transition-all flex items-center gap-1.5 ${
              activeTab === 'jira'
                ? 'border-blue-500 text-slate-800'
                : 'border-transparent text-slate-400 hover:text-slate-600'
            }`}
          >
            <Trello className="w-3.5 h-3.5" />
            Jira Cloud
          </button>
        </div>

        {/* Modal Scrollable Content */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {activeTab === 'gmail' && (
            <div className="space-y-5">
              <div className="p-4 bg-red-50/50 border border-red-100 rounded-xl flex gap-3">
                <Shield className="w-5 h-5 text-red-600 shrink-0 mt-0.5" />
                <div className="space-y-1">
                  <h4 className="text-xs font-bold text-slate-800">Domain-Wide Delegation (DWD) Required</h4>
                  <p className="text-[11px] text-slate-500 leading-relaxed">
                    Google Workspace limits global email collection without Domain-Wide Delegation. By creating a service account and granting API scopes, you authorize Lore's background stream worker to harvest email threads from your members without manual, individual OAuth handshakes.
                  </p>
                </div>
              </div>

              <div className="space-y-4">
                <div className="flex gap-3">
                  <div className="w-6 h-6 rounded-full bg-slate-100 flex items-center justify-center text-xs font-bold text-slate-600 shrink-0">1</div>
                  <div className="space-y-1 pt-0.5">
                    <h5 className="text-xs font-bold text-slate-800">Create Service Account</h5>
                    <p className="text-[11px] text-slate-500 leading-relaxed">
                      Go to the <a href="https://console.cloud.google.com" target="_blank" rel="noreferrer" className="text-blue-600 inline-flex items-center gap-0.5 hover:underline font-semibold">Google Cloud Console <ExternalLink className="w-3 h-3" /></a>, create or select a project, then go to <strong>IAM & Admin &gt; Service Accounts</strong>. Click <strong>Create Service Account</strong> and grant it no specific roles (it just needs project access).
                    </p>
                  </div>
                </div>

                <div className="flex gap-3">
                  <div className="w-6 h-6 rounded-full bg-slate-100 flex items-center justify-center text-xs font-bold text-slate-600 shrink-0">2</div>
                  <div className="space-y-1 pt-0.5">
                    <h5 className="text-xs font-bold text-slate-800">Generate JSON Key File</h5>
                    <p className="text-[11px] text-slate-500 leading-relaxed">
                      Once created, click on your Service Account, go to the <strong>Keys</strong> tab, click <strong>Add Key &gt; Create new key</strong>, select <strong>JSON</strong>, and download the key. This is the `.json` file you must upload on the form.
                    </p>
                  </div>
                </div>

                <div className="flex gap-3">
                  <div className="w-6 h-6 rounded-full bg-slate-100 flex items-center justify-center text-xs font-bold text-slate-600 shrink-0">3</div>
                  <div className="space-y-1 pt-0.5">
                    <h5 className="text-xs font-bold text-slate-800">Configure Domain-Wide Delegation</h5>
                    <p className="text-[11px] text-slate-500 leading-relaxed">
                      Open the <a href="https://admin.google.com" target="_blank" rel="noreferrer" className="text-blue-600 inline-flex items-center gap-0.5 hover:underline font-semibold">Google Admin Console <ExternalLink className="w-3 h-3" /></a> with a Super Admin account. Navigate to <strong>Security &gt; Access and data control &gt; API controls</strong>. Click <strong>Manage Domain Wide Delegation</strong>, then <strong>Add new</strong>.
                    </p>
                  </div>
                </div>

                <div className="flex gap-3">
                  <div className="w-6 h-6 rounded-full bg-slate-100 flex items-center justify-center text-xs font-bold text-slate-600 shrink-0">4</div>
                  <div className="space-y-2.5 pt-0.5 flex-1">
                    <h5 className="text-xs font-bold text-slate-800">Register API OAuth Scopes</h5>
                    <p className="text-[11px] text-slate-500 leading-relaxed">
                      Enter the <strong>Client ID</strong> (also known as `Unique ID` or `oauth2ClientId`) found in your service account JSON key file. In the <strong>OAuth Scopes</strong> text field, copy and add the following two scopes exactly:
                    </p>
                    <div className="space-y-2 max-w-md">
                      <CopyableScope scope="https://www.googleapis.com/auth/gmail.readonly" isCopied={copiedText === "https://www.googleapis.com/auth/gmail.readonly"} onCopy={handleCopy} />
                      <CopyableScope scope="https://www.googleapis.com/auth/gmail.metadata" isCopied={copiedText === "https://www.googleapis.com/auth/gmail.metadata"} onCopy={handleCopy} />
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'slack' && (
            <div className="space-y-5">
              <div className="p-4 bg-purple-50/50 border border-purple-100 rounded-xl flex gap-3">
                <Key className="w-5 h-5 text-purple-600 shrink-0 mt-0.5" />
                <div className="space-y-1">
                  <h4 className="text-xs font-bold text-slate-800">Slack Event Scopes and Socket Mode</h4>
                  <p className="text-[11px] text-slate-500 leading-relaxed">
                    Sync public and private channels by creating a custom Slack App in your workspace. This grants the bot access to real-time events without requiring full system administrative authorization.
                  </p>
                </div>
              </div>

              <div className="space-y-4">
                <div className="flex gap-3">
                  <div className="w-6 h-6 rounded-full bg-slate-100 flex items-center justify-center text-xs font-bold text-slate-600 shrink-0">1</div>
                  <div className="space-y-1 pt-0.5">
                    <h5 className="text-xs font-bold text-slate-800">Create a Slack App</h5>
                    <p className="text-[11px] text-slate-500 leading-relaxed">
                      Go to the <a href="https://api.slack.com/apps" target="_blank" rel="noreferrer" className="text-blue-600 inline-flex items-center gap-0.5 hover:underline font-semibold">Slack Developer Console <ExternalLink className="w-3 h-3" /></a>, click <strong>Create New App</strong>, select <strong>From scratch</strong>, name it (e.g. `Lore Sync`), and select your target workspace.
                    </p>
                  </div>
                </div>

                <div className="flex gap-3">
                  <div className="w-6 h-6 rounded-full bg-slate-100 flex items-center justify-center text-xs font-bold text-slate-600 shrink-0">2</div>
                  <div className="space-y-2.5 pt-0.5 flex-1">
                    <h5 className="text-xs font-bold text-slate-800">Configure Bot Token Scopes</h5>
                    <p className="text-[11px] text-slate-500 leading-relaxed">
                      Navigate to <strong>OAuth & Permissions &gt; Scopes &gt; Bot Token Scopes</strong>. Add the following required read-only subscription scopes:
                    </p>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2 max-w-lg">
                      <CopyableScope scope="channels:history" isCopied={copiedText === "channels:history"} onCopy={handleCopy} />
                      <CopyableScope scope="groups:history" isCopied={copiedText === "groups:history"} onCopy={handleCopy} />
                      <CopyableScope scope="im:history" isCopied={copiedText === "im:history"} onCopy={handleCopy} />
                      <CopyableScope scope="mpim:history" isCopied={copiedText === "mpim:history"} onCopy={handleCopy} />
                      <CopyableScope scope="users:read" isCopied={copiedText === "users:read"} onCopy={handleCopy} />
                    </div>
                  </div>
                </div>

                <div className="flex gap-3">
                  <div className="w-6 h-6 rounded-full bg-slate-100 flex items-center justify-center text-xs font-bold text-slate-600 shrink-0">3</div>
                  <div className="space-y-1 pt-0.5">
                    <h5 className="text-xs font-bold text-slate-800">Install to Workspace and Retrieve Token</h5>
                    <p className="text-[11px] text-slate-500 leading-relaxed">
                      Scroll to the top of the <strong>OAuth & Permissions</strong> page and click <strong>Install to Workspace</strong>. Confirm the access screen. Once successfully re-routed, copy the generated <strong>Bot User OAuth Token</strong> (starts with <code>xoxb-</code>) and paste it into the Slack Bot Token form field.
                    </p>
                  </div>
                </div>

                <div className="flex gap-3">
                  <div className="w-6 h-6 rounded-full bg-slate-100 flex items-center justify-center text-xs font-bold text-slate-600 shrink-0">4</div>
                  <div className="space-y-2 pt-0.5 flex-1">
                    <h5 className="text-xs font-bold text-slate-800">Enable Socket Mode (Optional App Token)</h5>
                    <p className="text-[11px] text-slate-500 leading-relaxed">
                      To support private firewalled servers without configuring public inbound webhooks, enable <strong>Socket Mode</strong>. Navigate to the App Settings, click <strong>Socket Mode &gt; Enable</strong>. Name the token and click Generate. Copy this generated App Token (starts with <code>xapp-</code>) and paste it into the optional App Token field.
                    </p>
                    <div className="max-w-md">
                      <CopyableScope scope="connections:write" isCopied={copiedText === "connections:write"} onCopy={handleCopy} />
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'jira' && (
            <div className="space-y-5">
              <div className="p-4 bg-blue-50/50 border border-blue-100 rounded-xl flex gap-3">
                <Trello className="w-5 h-5 text-blue-600 shrink-0 mt-0.5" />
                <div className="space-y-1">
                  <h4 className="text-xs font-bold text-slate-800">Atlassian Jira Ingestion</h4>
                  <p className="text-[11px] text-slate-500 leading-relaxed">
                    Lore indexes tickets, epics, backlogs, and team workflow vectors to analyze velocity and decision records. Use your Atlassian API key to build a read-only index of sprint boards.
                  </p>
                </div>
              </div>

              <div className="space-y-4">
                <div className="flex gap-3">
                  <div className="w-6 h-6 rounded-full bg-slate-100 flex items-center justify-center text-xs font-bold text-slate-600 shrink-0">1</div>
                  <div className="space-y-1 pt-0.5">
                    <h5 className="text-xs font-bold text-slate-800">Generate Atlassian API Token</h5>
                    <p className="text-[11px] text-slate-500 leading-relaxed">
                      Log in to your Atlassian dashboard, and navigate directly to your <a href="https://id.atlassian.com/manage-profile/security/api-tokens" target="_blank" rel="noreferrer" className="text-blue-600 inline-flex items-center gap-0.5 hover:underline font-semibold">Atlassian API Tokens Console <ExternalLink className="w-3 h-3" /></a>.
                    </p>
                  </div>
                </div>

                <div className="flex gap-3">
                  <div className="w-6 h-6 rounded-full bg-slate-100 flex items-center justify-center text-xs font-bold text-slate-600 shrink-0">2</div>
                  <div className="space-y-1 pt-0.5">
                    <h5 className="text-xs font-bold text-slate-800">Create a Secure Token</h5>
                    <p className="text-[11px] text-slate-500 leading-relaxed">
                      Click <strong>Create API token</strong>, enter a representative label (e.g. <code>Lore Knowledge Core</code>), and click <strong>Create</strong>. Copy this securely generated token string immediately as it will only be shown once.
                    </p>
                  </div>
                </div>

                <div className="flex gap-3">
                  <div className="w-6 h-6 rounded-full bg-slate-100 flex items-center justify-center text-xs font-bold text-slate-600 shrink-0">3</div>
                  <div className="space-y-1 pt-0.5">
                    <h5 className="text-xs font-bold text-slate-800">Verify Administrator Email</h5>
                    <p className="text-[11px] text-slate-500 leading-relaxed">
                      Ensure that the administrator email address entered in the form is the identical account email used to generate this API token, and has read access to your target boards and backlogs.
                    </p>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Modal Footer */}
        <div className="px-6 py-4.5 border-t border-slate-100 flex justify-end bg-slate-50/50">
          <Button
            onClick={onClose}
            className="bg-blue-600 hover:bg-blue-700 text-white text-xs font-bold shadow-sm h-9 px-5"
          >
            I Understand
          </Button>
        </div>
      </div>
    </div>
  );
}
