import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import AppShell from '@/components/app-shell/AppShell';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { useIntegrationSetup } from '@/hooks/useIntegrationSetup';
import { KnowledgeBrain } from '@/components/onboarding/KnowledgeBrain';
import { AppIcon } from '@/components/onboarding/AppIcon';
import { ConnectionLine } from '@/components/onboarding/ConnectionLine';
import { calculateCircularPosition, type Point } from '@/utils/svgPaths';
import { CredentialHelpModal } from '@/components/onboarding/CredentialHelpModal';
import {
  testConnection,
  saveConfiguration,
  getExistingConnections,
  type GmailConfig,
  type SlackConfig,
  type JiraConfig,
  type ConnectionStatus,
  type IntegrationCredentials,
} from '@/services/integrationSetupApi';
import { setEnvVar, runIngestion } from '@/services/envVarsApi';
import {
  Shield,
  Sparkles,
  AlertCircle,
  Upload,
  Trash2,
  Database,
  Plug,
  ExternalLink,
  CheckCircle2,
  XCircle,
  Loader2,
} from 'lucide-react';

export default function Setup() {
  const { setupData, updateSetupData, clearDraft } = useIntegrationSetup();
  const [hoveredIntegration, setHoveredIntegration] = useState<string | null>(null);
  const [containerSize, setContainerSize] = useState({ width: 0, height: 0 });
  const fileInputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const navigate = useNavigate();

  const [backendConnections, setBackendConnections] = useState<ConnectionStatus | null>(null);

  const [testingStates, setTestingStates] = useState<Record<string, {
    status: 'idle' | 'testing' | 'success' | 'failed';
    message?: string;
  }>>({
    gmail: { status: 'idle' },
    slack: { status: 'idle' },
    jira: { status: 'idle' },
  });

  const [savingState, setSavingState] = useState<'idle' | 'saving' | 'success' | 'failed'>('idle');
  const [savingError, setSavingError] = useState<string | null>(null);
  const [showSkipConfirm, setShowSkipConfirm] = useState(false);
  const [helpModalOpen, setHelpModalOpen] = useState(false);
  const [helpModalTab, setHelpModalTab] = useState<'gmail' | 'slack' | 'jira'>('gmail');

  const handleTestConnection = async (source: 'gmail' | 'slack' | 'jira') => {
    setTestingStates((prev) => ({
      ...prev,
      [source]: { status: 'testing' },
    }));

    const config = setupData[source];
    const res = await testConnection(source, config);

    if (res.success) {
      setTestingStates((prev) => ({
        ...prev,
        [source]: { status: 'success', message: res.message || 'Connection successful!' },
      }));
    } else {
      setTestingStates((prev) => ({
        ...prev,
        [source]: { status: 'failed', message: res.error || 'Connection failed. Please check credentials.' },
      }));
    }
  };

  const handleSaveAndContinue = async () => {
    setSavingState('saving');
    setSavingError(null);

    const jira = setupData.jira;

    // Real Jira ingestion path: encrypt the three credentials via client-admin,
    // then trigger the Jira ingestion runner. Gmail/Slack keep the existing
    // mock/draft save behavior below.
    if (jira.enabled && jira.baseUrl && jira.email && jira.apiToken) {
      try {
        await setEnvVar('JIRA_BASE_URL', jira.baseUrl);
        await setEnvVar('JIRA_EMAIL', jira.email);
        await setEnvVar('JIRA_API_TOKEN', jira.apiToken);

        const jiraProjectKey = (jira.projectKey || '').trim();
        await runIngestion({ source: 'jira', jiraProjectKey });
      } catch (err) {
        setSavingState('failed');
        setSavingError(
          err instanceof Error ? err.message : 'Failed to start Jira ingestion'
        );
        return;
      }
    }

    // Persist gmail/slack (and the full draft) via the existing configuration save.
    const res = await saveConfiguration(setupData);
    if (res.success) {
      setSavingState('success');
      navigate('/onboarding');
    } else {
      setSavingState('failed');
      setSavingError(res.error || 'Failed to save configuration');
    }
  };

  const hasSuccessfulTest =
    Object.values(testingStates).some((state) => state.status === 'success') ||
    !!(backendConnections && (
      (backendConnections.gmail?.connected && setupData.gmail.enabled) ||
      (backendConnections.slack?.connected && setupData.slack.enabled) ||
      (backendConnections.jira?.connected && setupData.jira.enabled)
    ));

  // Restore active tab from local storage
  const [activeTab, setActiveTab] = useState(() => {
    return localStorage.getItem('loremind_setup_active_tab') || 'gmail';
  });

  const handleTabChange = (value: string) => {
    setActiveTab(value);
    localStorage.setItem('loremind_setup_active_tab', value);
  };

  const [viewportWidth, setViewportWidth] = useState(typeof window !== 'undefined' ? window.innerWidth : 1024);

  // Monitor container dimensions to ensure circular layout is responsive
  useEffect(() => {
    const updateSize = () => {
      if (containerRef.current) {
        setContainerSize({
          width: containerRef.current.offsetWidth,
          height: containerRef.current.offsetHeight,
        });
      }
      setViewportWidth(window.innerWidth);
    };

    updateSize();
    window.addEventListener('resize', updateSize);
    return () => window.removeEventListener('resize', updateSize);
  }, []);

  const isMobile = viewportWidth < 640;

  // Fetch production connection status on mount to sync/pre-populate
  useEffect(() => {
    let active = true;
    async function loadStatus() {
      try {
        const statuses = await getExistingConnections();
        if (active) {
          setBackendConnections(statuses);
          
          // If already connected on the production backend but disabled locally, sync with local draft
          const updates: Partial<Required<IntegrationCredentials>> = {};
          if (statuses.gmail.connected && !setupData.gmail.enabled) {
            updates.gmail = { ...setupData.gmail, enabled: true };
          }
          if (statuses.slack.connected && !setupData.slack.enabled) {
            updates.slack = { ...setupData.slack, enabled: true };
          }
          if (statuses.jira.connected && !setupData.jira.enabled) {
            updates.jira = { ...setupData.jira, enabled: true };
          }
          if (Object.keys(updates).length > 0) {
            updateSetupData(updates);
          }
        }
      } catch (err) {
        console.error('Failed to load existing connections', err);
      }
    }
    loadStatus();
    return () => {
      active = false;
    };
  }, []);

  // ==========================================
  // Form Validations
  // ==========================================

  // --- Gmail ---
  const isGmailValid = (config: GmailConfig) => {
    if (!config.domain || !config.adminEmail) return false;
    // Primary domain regex
    const domainRegex = /^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/;
    if (!domainRegex.test(config.domain)) return false;
    // Super Admin Email regex
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(config.adminEmail)) return false;
    // Email domain match
    if (!config.adminEmail.toLowerCase().endsWith('@' + config.domain.toLowerCase())) return false;
    // JSON Key uploaded
    if (!config.serviceAccountKey) return false;
    // DWD Checked
    if (!config.dwdEnabled) return false;
    return true;
  };

  const isGmailDraft = (config: GmailConfig) => {
    return !!(
      config.domain ||
      config.adminEmail ||
      config.serviceAccountKey ||
      config.additionalDomains.length > 0 ||
      config.dwdEnabled
    );
  };

  const getGmailStatus = (config: GmailConfig): 'Ready' | 'Draft Saved' | 'Not Configured' => {
    if (isGmailValid(config)) return 'Ready';
    if (isGmailDraft(config)) return 'Draft Saved';
    return 'Not Configured';
  };

  // --- Slack ---
  const isSlackValid = (config: SlackConfig) => {
    if (!config.workspaceDomain || !config.botToken) return false;
    if (!config.botToken.startsWith('xoxb-')) return false;
    if (config.appToken && !config.appToken.startsWith('xapp-')) return false;
    return true;
  };

  const isSlackDraft = (config: SlackConfig) => {
    return !!(
      config.workspaceDomain ||
      config.botToken ||
      config.appToken ||
      config.isEnterpriseGrid ||
      config.appTier !== 'marketplace'
    );
  };

  const getSlackStatus = (config: SlackConfig): 'Ready' | 'Draft Saved' | 'Not Configured' => {
    if (isSlackValid(config)) return 'Ready';
    if (isSlackDraft(config)) return 'Draft Saved';
    return 'Not Configured';
  };

  // --- Jira ---
  const isJiraValid = (config: JiraConfig) => {
    if (!config.baseUrl || !config.email) return false;
    try {
      const url = new URL(config.baseUrl);
      if (url.protocol !== 'http:' && url.protocol !== 'https:') return false;
    } catch {
      return false;
    }
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(config.email)) return false;
    if (config.authType === 'api_token' && !config.apiToken) return false;
    return true;
  };

  const isJiraDraft = (config: JiraConfig) => {
    return !!(
      config.baseUrl ||
      config.email ||
      config.apiToken ||
      config.authType !== 'api_token'
    );
  };

  const getJiraStatus = (config: JiraConfig): 'Ready' | 'Draft Saved' | 'Not Configured' => {
    if (isJiraValid(config)) return 'Ready';
    if (isJiraDraft(config)) return 'Draft Saved';
    return 'Not Configured';
  };

  // ==========================================
  // Ingestion Definitions
  // ==========================================
  const integrationsList = [
    {
      id: 'gmail',
      name: 'Gmail',
      logo: '/images/gmail.png',
      description: 'Email threads and communications',
      connected: setupData.gmail.enabled,
      connecting: false,
      status: getGmailStatus(setupData.gmail),
      toggle: () =>
        updateSetupData({
          gmail: { ...setupData.gmail, enabled: !setupData.gmail.enabled },
        }),
    },
    {
      id: 'slack',
      name: 'Slack',
      logo: '/images/slack.png',
      description: 'Team conversations and decisions',
      connected: setupData.slack.enabled,
      connecting: false,
      status: getSlackStatus(setupData.slack),
      toggle: () =>
        updateSetupData({
          slack: { ...setupData.slack, enabled: !setupData.slack.enabled },
        }),
    },
    {
      id: 'jira',
      name: 'Jira',
      logo: '/images/jira.jpg',
      description: 'Project tasks and issue tracking',
      connected: setupData.jira.enabled,
      connecting: false,
      status: getJiraStatus(setupData.jira),
      toggle: () =>
        updateSetupData({
          jira: { ...setupData.jira, enabled: !setupData.jira.enabled },
        }),
    },
  ];

  const totalIntegrations = integrationsList.length;
  const configuredCount = integrationsList.filter((item) => item.status !== 'Not Configured').length;
  const readyCount = integrationsList.filter((item) => item.status === 'Ready').length;
  const completionPercentage = Math.round((readyCount / totalIntegrations) * 100);

  // Layout parameters for high-fidelity circular nodes
  const iconRadius = 140;
  const brainCenter: Point = {
    x: containerSize.width / 2,
    y: containerSize.height / 2,
  };

  // Handle JSON key file uploads
  const handleKeyFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      const reader = new FileReader();
      reader.onload = (event) => {
        const text = event.target?.result as string;
        try {
          JSON.parse(text); // Verify valid JSON
          updateSetupData({
            gmail: {
              ...setupData.gmail,
              serviceAccountKeyName: file.name,
              serviceAccountKey: text,
            },
          });
        } catch (err) {
          console.error('Failed to parse Google JSON key:', err);
          setSavingError('Invalid JSON format: Uploaded file is not a valid Google Cloud Service Account credentials JSON.');
          if (fileInputRef.current) fileInputRef.current.value = '';
        }
      };
      reader.readAsText(file);
    }
  };

  const removeKeyFile = () => {
    updateSetupData({
      gmail: {
        ...setupData.gmail,
        serviceAccountKeyName: '',
        serviceAccountKey: null,
      },
    });
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const handleAutofillDemoData = () => {
    updateSetupData({
      gmail: {
        enabled: true,
        domain: 'loremind.com',
        adminEmail: 'admin@loremind.com',
        serviceAccountKey: JSON.stringify({
          type: 'service_account',
          project_id: 'loremind-production',
          private_key: '-----BEGIN PRIVATE KEY-----\nMOCK_PRIVATE_KEY_VALUE_FOR_LOREMIND_INGESTION_FLOW\n-----END PRIVATE KEY-----\n',
          client_email: 'loremind-sync@loremind-production.iam.gserviceaccount.com',
        }),
        serviceAccountKeyName: 'loremind-google-key.json',
        additionalDomains: [],
        dwdEnabled: true,
      },
      slack: {
        enabled: true,
        workspaceDomain: 'loremind.slack.com',
        botToken: 'xoxb-mock-bot-token-for-testing-only',
        appToken: 'xapp-mock-app-token-for-socket-mode',
        isEnterpriseGrid: false,
        appTier: 'marketplace',
      },
      jira: {
        enabled: true,
        baseUrl: 'https://loremind.atlassian.net',
        email: 'admin@loremind.com',
        authType: 'api_token',
        apiToken: 'mock-atlassian-api-token-value',
        oauthToken: '',
        projectKey: 'LORE',
      },
    });

    setTestingStates({
      gmail: { status: 'success', message: 'Successfully verified connection to Gmail (Auto-filled Demo)' },
      slack: { status: 'success', message: 'Successfully verified connection to Slack (Auto-filled Demo)' },
      jira: { status: 'success', message: 'Successfully verified connection to Jira (Auto-filled Demo)' },
    });
  };

  const getBadgeVariant = (status: 'Ready' | 'Draft Saved' | 'Not Configured') => {
    if (status === 'Ready') return 'default'; // dark/primary
    if (status === 'Draft Saved') return 'secondary';
    return 'outline';
  };

  const getStatusClasses = (status: 'Ready' | 'Draft Saved' | 'Not Configured') => {
    if (status === 'Ready') return 'bg-emerald-50 text-emerald-700 border-emerald-200/50 hover:bg-emerald-50';
    if (status === 'Draft Saved') return 'bg-amber-50 text-amber-700 border-amber-200/50 hover:bg-amber-50';
    return 'bg-slate-100 text-slate-400 border-slate-200/50';
  };

  return (
    <AppShell
      title="Connect Your Knowledge Sources"
      subtitle="Configure integrations required for Lore ingestion"
    >
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8 items-start">
        {/* Left Column + Main Content (spanning 2 cols on wide screens) */}
        <div className="lg:col-span-2 space-y-6">
          {/* Main Credentials Configuration Card */}
          <Card className="shadow-sm overflow-hidden border-slate-200/70">
            <Tabs value={activeTab} onValueChange={handleTabChange} className="w-full">
              {/* Custom Header with Tabs selection & Badge Status indicators */}
              <div className="border-b border-slate-150 bg-slate-50/50 px-6 py-4 flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div>
                  <CardTitle className="text-[15px] font-semibold text-slate-900">
                    Integration Credentials Setup
                  </CardTitle>
                  <CardDescription className="text-xs">
                    Credentials are automatically autosaved to browser draft cache.
                  </CardDescription>
                </div>
                <TabsList className="bg-slate-100/80 p-0.5 border border-slate-200/50 rounded-lg flex self-start md:self-auto">
                  {integrationsList.map((item) => (
                    <TabsTrigger
                      key={item.id}
                      value={item.id}
                      className="px-4 py-1.5 rounded-md text-xs font-semibold data-[state=active]:bg-white data-[state=active]:text-slate-900 data-[state=active]:shadow-sm transition-all"
                    >
                      <div className="flex items-center gap-1.5">
                        <span>{item.name}</span>
                        <Badge
                          variant={getBadgeVariant(item.status)}
                          className={`text-[9px] px-1 py-0 h-4 rounded font-bold uppercase tracking-wider ${getStatusClasses(
                            item.status
                          )}`}
                        >
                          {item.status === 'Ready'
                            ? 'Ready'
                            : item.status === 'Draft Saved'
                            ? 'Draft'
                            : 'Empty'}
                        </Badge>
                      </div>
                    </TabsTrigger>
                  ))}
                </TabsList>
              </div>

              {/* GMAIL SETUP TAB */}
              <TabsContent value="gmail" className="p-6 focus-visible:outline-none space-y-6">
                <div className="flex items-center justify-between pb-4 border-b border-slate-100">
                  <div className="space-y-0.5">
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-semibold text-slate-800">Gmail Workspace Indexer</h3>
                      <button
                        type="button"
                        onClick={() => {
                          setHelpModalTab('gmail');
                          setHelpModalOpen(true);
                        }}
                        className="text-[10px] font-extrabold text-blue-600 hover:text-blue-700 bg-blue-50 hover:bg-blue-100/80 px-2 py-0.5 rounded transition-all shrink-0 uppercase tracking-wider"
                      >
                        Setup Guide
                      </button>
                    </div>
                    <p className="text-xs text-slate-500">
                      Enable read-only mail thread syncing via Google G Suite.
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    <Label htmlFor="gmail-enable" className="text-xs font-bold text-slate-500 uppercase">
                      {setupData.gmail.enabled ? 'Enabled' : 'Disabled'}
                    </Label>
                    <Switch
                      id="gmail-enable"
                      checked={setupData.gmail.enabled}
                      onCheckedChange={() =>
                        updateSetupData({
                          gmail: { ...setupData.gmail, enabled: !setupData.gmail.enabled },
                        })
                      }
                    />
                  </div>
                </div>

                {backendConnections?.gmail.connected && (
                  <div className="p-3.5 bg-emerald-50/50 border border-emerald-100/60 rounded-xl flex items-center justify-between animate-in fade-in duration-300">
                    <div className="flex items-center gap-2 text-xs font-semibold text-emerald-800">
                      <CheckCircle2 className="w-4 h-4 text-emerald-600 animate-pulse shrink-0" />
                      <span>Production Connected</span>
                      {backendConnections.gmail.last_sync && (
                        <span className="text-[10px] text-emerald-600/80 font-normal hidden sm:inline">
                          • Last synced {new Date(backendConnections.gmail.last_sync).toLocaleString()}
                        </span>
                      )}
                    </div>
                    <Badge variant="outline" className="bg-emerald-100 text-emerald-700 border-emerald-200 uppercase text-[9px] font-extrabold tracking-wider">
                      Live Ingesting
                    </Badge>
                  </div>
                )}

                <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                  {/* Primary Domain */}
                  <div className="space-y-1.5">
                    <Label htmlFor="g-domain" className="text-xs font-semibold text-slate-700">
                      Primary Domain <span className="text-rose-500">*</span>
                    </Label>
                    <Input
                      id="g-domain"
                      value={setupData.gmail.domain}
                      onChange={(e) =>
                        updateSetupData({
                          gmail: { ...setupData.gmail, domain: e.target.value },
                        })
                      }
                      placeholder="e.g. acme.com"
                      className="text-xs"
                    />
                    {setupData.gmail.enabled &&
                      setupData.gmail.domain &&
                      !/^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/.test(setupData.gmail.domain) && (
                        <div className="flex items-center gap-1 text-[11px] text-rose-600 mt-1">
                          <AlertCircle className="w-3 h-3" />
                          <span>Invalid domain format.</span>
                        </div>
                      )}
                  </div>

                  {/* Super Admin Email */}
                  <div className="space-y-1.5">
                    <Label htmlFor="g-email" className="text-xs font-semibold text-slate-700">
                      Super Admin Email <span className="text-rose-500">*</span>
                    </Label>
                    <Input
                      id="g-email"
                      type="email"
                      value={setupData.gmail.adminEmail}
                      onChange={(e) =>
                        updateSetupData({
                          gmail: { ...setupData.gmail, adminEmail: e.target.value },
                        })
                      }
                      placeholder="e.g. admin@acme.com"
                      className="text-xs"
                    />
                    {setupData.gmail.enabled && setupData.gmail.adminEmail && (
                      <>
                        {!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(setupData.gmail.adminEmail) && (
                          <div className="flex items-center gap-1 text-[11px] text-rose-600 mt-1">
                            <AlertCircle className="w-3 h-3" />
                            <span>Invalid email format.</span>
                          </div>
                        )}
                        {/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(setupData.gmail.adminEmail) &&
                          setupData.gmail.domain &&
                          !setupData.gmail.adminEmail
                            .toLowerCase()
                            .endsWith('@' + setupData.gmail.domain.toLowerCase()) && (
                            <div className="flex items-center gap-1 text-[11px] text-rose-600 mt-1">
                              <AlertCircle className="w-3 h-3" />
                              <span>Email must belong to primary domain.</span>
                            </div>
                          )}
                      </>
                    )}
                  </div>

                  {/* Additional Domains */}
                  <div className="space-y-1.5 md:col-span-2">
                    <Label htmlFor="g-add-domains" className="text-xs font-semibold text-slate-700">
                      Additional Target Domains <span className="text-slate-400 font-normal">(Optional)</span>
                    </Label>
                    <Input
                      id="g-add-domains"
                      value={Array.isArray(setupData.gmail.additionalDomains) ? setupData.gmail.additionalDomains.join(', ') : ''}
                      onChange={(e) => {
                        const arr = e.target.value ? e.target.value.split(',').map((s) => s.trim()) : [];
                        updateSetupData({
                          gmail: { ...setupData.gmail, additionalDomains: arr },
                        });
                      }}
                      placeholder="Comma-separated secondary domains (e.g. acme-subsidiary.com, acme.co)"
                      className="text-xs"
                    />
                  </div>

                  {/* Service Account Key File Upload */}
                  <div className="space-y-2 md:col-span-2">
                    <Label className="text-xs font-semibold text-slate-700">
                      Google Service Account JSON Key <span className="text-rose-500">*</span>
                    </Label>
                    {setupData.gmail.serviceAccountKeyName ? (
                      <div className="flex items-center justify-between p-3 bg-emerald-50/60 border border-emerald-200/50 rounded-lg">
                        <div className="flex items-center gap-2.5">
                          <div className="w-8 h-8 rounded-lg bg-emerald-100 flex items-center justify-center text-emerald-700">
                            <Database className="w-4 h-4" />
                          </div>
                          <div className="min-w-0">
                            <div className="text-xs font-semibold text-slate-800 truncate">
                              {setupData.gmail.serviceAccountKeyName}
                            </div>
                            <div className="text-[10px] text-emerald-600 font-medium">
                              Ready for secure stream authentication
                            </div>
                          </div>
                        </div>
                        <Button
                          onClick={removeKeyFile}
                          variant="ghost"
                          size="icon"
                          className="text-slate-400 hover:text-rose-600 hover:bg-rose-50"
                        >
                          <Trash2 className="w-4 h-4" />
                        </Button>
                      </div>
                    ) : (
                      <div
                        onClick={() => fileInputRef.current?.click()}
                        className="cursor-pointer border-2 border-dashed border-slate-200 hover:border-blue-400 rounded-xl p-6 flex flex-col items-center justify-center text-center space-y-2 bg-slate-50/30 transition-all"
                      >
                        <Upload className="w-6 h-6 text-slate-400" />
                        <div className="text-xs font-semibold text-slate-700">
                          Upload GSuite Key JSON File
                        </div>
                        <div className="text-[11px] text-slate-400">
                          Required for Domain-Wide Delegation authentication.
                        </div>
                        <input
                          ref={fileInputRef}
                          type="file"
                          accept=".json"
                          onChange={handleKeyFileUpload}
                          className="hidden"
                        />
                      </div>
                    )}
                    {setupData.gmail.enabled && !setupData.gmail.serviceAccountKeyName && (
                      <div className="flex items-center gap-1 text-[11px] text-rose-600">
                        <AlertCircle className="w-3 h-3" />
                        <span>Google Service Account key file is required when Gmail is enabled.</span>
                      </div>
                    )}
                  </div>

                  {/* DWD Checkbox */}
                  <div className="md:col-span-2 p-3.5 bg-slate-50 border border-slate-200/60 rounded-xl flex items-start gap-3">
                    <input
                      id="g-dwd"
                      type="checkbox"
                      checked={setupData.gmail.dwdEnabled}
                      onChange={(e) =>
                        updateSetupData({
                          gmail: { ...setupData.gmail, dwdEnabled: e.target.checked },
                        })
                      }
                      className="mt-1 w-4 h-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                    />
                    <div className="space-y-0.5">
                      <Label htmlFor="g-dwd" className="text-xs font-semibold text-slate-800 cursor-pointer">
                        Domain-Wide Delegation (DWD) Enabled <span className="text-rose-500">*</span>
                      </Label>
                      <p className="text-[11px] text-slate-400 leading-relaxed">
                        Authorize this client to impersonate Workspace members globally. DWD is mandatory for automated background metadata harvesting.
                      </p>
                      {setupData.gmail.enabled && !setupData.gmail.dwdEnabled && (
                        <div className="flex items-center gap-1 text-[11px] text-rose-600 pt-1">
                          <AlertCircle className="w-3 h-3" />
                          <span>DWD must be enabled on the Google console and checked here.</span>
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                {/* Connection Test Section */}
                {setupData.gmail.enabled && (
                  <div className="mt-6 pt-5 border-t border-slate-100 flex flex-col sm:flex-row sm:items-center justify-between gap-4 animate-in fade-in duration-300">
                    <div className="flex-1 min-w-0">
                      {testingStates.gmail.status === 'success' && (
                        <div className="flex items-start gap-2 text-emerald-600 text-xs">
                          <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5 animate-bounce" />
                          <span className="font-medium">{testingStates.gmail.message}</span>
                        </div>
                      )}
                      {testingStates.gmail.status === 'failed' && (
                        <div className="flex items-start gap-2 text-rose-600 text-xs">
                          <XCircle className="w-4 h-4 shrink-0 mt-0.5" />
                          <span className="font-medium">{testingStates.gmail.message}</span>
                        </div>
                      )}
                      {testingStates.gmail.status === 'idle' && (
                        <p className="text-[11px] text-slate-400">
                          Verify Gmail credentials by running a test call before continuing.
                        </p>
                      )}
                      {testingStates.gmail.status === 'testing' && (
                        <div className="flex items-center gap-2 text-blue-600 text-xs font-medium">
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          <span>Pinging Gmail API stream authentication...</span>
                        </div>
                      )}
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={!isGmailValid(setupData.gmail) || testingStates.gmail.status === 'testing'}
                      onClick={() => handleTestConnection('gmail')}
                      className="text-xs font-semibold h-9 shrink-0 border-blue-200/60 hover:bg-blue-50/50 hover:text-blue-700"
                    >
                      {testingStates.gmail.status === 'testing' ? 'Testing...' : 'Test Connection'}
                    </Button>
                  </div>
                )}
              </TabsContent>

              {/* SLACK SETUP TAB */}
              <TabsContent value="slack" className="p-6 focus-visible:outline-none space-y-6">
                <div className="flex items-center justify-between pb-4 border-b border-slate-100">
                  <div className="space-y-0.5">
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-semibold text-slate-800">Slack Connection Settings</h3>
                      <button
                        type="button"
                        onClick={() => {
                          setHelpModalTab('slack');
                          setHelpModalOpen(true);
                        }}
                        className="text-[10px] font-extrabold text-blue-600 hover:text-blue-700 bg-blue-50 hover:bg-blue-100/80 px-2 py-0.5 rounded transition-all shrink-0 uppercase tracking-wider"
                      >
                        Setup Guide
                      </button>
                    </div>
                    <p className="text-xs text-slate-500">
                      Configure bot event subscriptions and workspace indexing scopes.
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    <Label htmlFor="slack-enable" className="text-xs font-bold text-slate-500 uppercase">
                      {setupData.slack.enabled ? 'Enabled' : 'Disabled'}
                    </Label>
                    <Switch
                      id="slack-enable"
                      checked={setupData.slack.enabled}
                      onCheckedChange={() =>
                        updateSetupData({
                          slack: { ...setupData.slack, enabled: !setupData.slack.enabled },
                        })
                      }
                    />
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                  {/* Workspace Domain */}
                  <div className="space-y-1.5 md:col-span-2">
                    <Label htmlFor="s-workspace" className="text-xs font-semibold text-slate-700">
                      Slack Workspace URL <span className="text-rose-500">*</span>
                    </Label>
                    <Input
                      id="s-workspace"
                      value={setupData.slack.workspaceDomain}
                      onChange={(e) =>
                        updateSetupData({
                          slack: { ...setupData.slack, workspaceDomain: e.target.value },
                        })
                      }
                      placeholder="e.g. acme-workspace.slack.com"
                      className="text-xs"
                    />
                    {setupData.slack.enabled && !setupData.slack.workspaceDomain && (
                      <div className="flex items-center gap-1 text-[11px] text-rose-600 mt-1">
                        <AlertCircle className="w-3 h-3" />
                        <span>Workspace URL is required.</span>
                      </div>
                    )}
                  </div>

                  {/* Bot Token */}
                  <div className="space-y-1.5">
                    <Label htmlFor="s-bot-token" className="text-xs font-semibold text-slate-700">
                      Slack Bot OAuth Token <span className="text-rose-500">*</span>
                    </Label>
                    <Input
                      id="s-bot-token"
                      type="password"
                      value={setupData.slack.botToken}
                      onChange={(e) =>
                        updateSetupData({
                          slack: { ...setupData.slack, botToken: e.target.value },
                        })
                      }
                      placeholder="xoxb-..."
                      className="text-xs font-mono"
                    />
                    {setupData.slack.enabled && (
                      <>
                        {!setupData.slack.botToken && (
                          <div className="flex items-center gap-1 text-[11px] text-rose-600 mt-1">
                            <AlertCircle className="w-3 h-3" />
                            <span>Bot OAuth token is required.</span>
                          </div>
                        )}
                        {setupData.slack.botToken && !setupData.slack.botToken.startsWith('xoxb-') && (
                          <div className="flex items-center gap-1 text-[11px] text-rose-600 mt-1">
                            <AlertCircle className="w-3 h-3" />
                            <span>Bot token must begin with "xoxb-".</span>
                          </div>
                        )}
                      </>
                    )}
                  </div>

                  {/* App Token */}
                  <div className="space-y-1.5">
                    <Label htmlFor="s-app-token" className="text-xs font-semibold text-slate-700">
                      Slack Socket Mode App Token <span className="text-slate-400 font-normal">(Optional)</span>
                    </Label>
                    <Input
                      id="s-app-token"
                      type="password"
                      value={setupData.slack.appToken}
                      onChange={(e) =>
                        updateSetupData({
                          slack: { ...setupData.slack, appToken: e.target.value },
                        })
                      }
                      placeholder="xapp-..."
                      className="text-xs font-mono"
                    />
                    {setupData.slack.appToken && !setupData.slack.appToken.startsWith('xapp-') && (
                      <div className="flex items-center gap-1 text-[11px] text-rose-600 mt-1">
                        <AlertCircle className="w-3 h-3" />
                        <span>App token must begin with "xapp-".</span>
                      </div>
                    )}
                  </div>

                  {/* App Tier Dropdown */}
                  <div className="space-y-1.5">
                    <Label htmlFor="s-tier" className="text-xs font-semibold text-slate-700">
                      Workspace Hosting Tier
                    </Label>
                    <select
                      id="s-tier"
                      value={setupData.slack.appTier}
                      onChange={(e) =>
                        updateSetupData({
                          slack: {
                            ...setupData.slack,
                            appTier: e.target.value as SlackConfig['appTier'],
                          },
                        })
                      }
                      className="w-full h-9 rounded-lg border border-slate-200/70 text-[12px] px-3 bg-white text-slate-800 outline-none focus:ring-2 focus:ring-blue-500/20"
                    >
                      <option value="marketplace">Standard Marketplace App</option>
                      <option value="tier3">Special Tier-3 Enterprise</option>
                      <option value="enterprise">Unlimited Enterprise Grid App</option>
                      <option value="restricted">Restricted Private Workspace</option>
                    </select>
                  </div>

                  {/* Enterprise Grid Toggle */}
                  <div className="p-3.5 bg-slate-50 border border-slate-200/60 rounded-xl flex items-center justify-between">
                    <div className="space-y-0.5">
                      <Label htmlFor="s-grid" className="text-xs font-semibold text-slate-800 cursor-pointer">
                        Multi-Workspace Enterprise Grid
                      </Label>
                      <p className="text-[10px] text-slate-400">
                        Check if syncing from multiple linked org tenants.
                      </p>
                    </div>
                    <Switch
                      id="s-grid"
                      checked={setupData.slack.isEnterpriseGrid}
                      onCheckedChange={(checked) =>
                        updateSetupData({
                          slack: { ...setupData.slack, isEnterpriseGrid: checked },
                        })
                      }
                    />
                  </div>
                </div>

                {/* Connection Test Section */}
                {setupData.slack.enabled && (
                  <div className="mt-6 pt-5 border-t border-slate-100 flex flex-col sm:flex-row sm:items-center justify-between gap-4 animate-in fade-in duration-300">
                    <div className="flex-1 min-w-0">
                      {testingStates.slack.status === 'success' && (
                        <div className="flex items-start gap-2 text-emerald-600 text-xs">
                          <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5 animate-bounce" />
                          <span className="font-medium">{testingStates.slack.message}</span>
                        </div>
                      )}
                      {testingStates.slack.status === 'failed' && (
                        <div className="flex items-start gap-2 text-rose-600 text-xs">
                          <XCircle className="w-4 h-4 shrink-0 mt-0.5" />
                          <span className="font-medium">{testingStates.slack.message}</span>
                        </div>
                      )}
                      {testingStates.slack.status === 'idle' && (
                        <p className="text-[11px] text-slate-400">
                          Verify Slack bot subscription connectivity with backend channels.
                        </p>
                      )}
                      {testingStates.slack.status === 'testing' && (
                        <div className="flex items-center gap-2 text-blue-600 text-xs font-medium">
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          <span>Probing Socket Mode app endpoints...</span>
                        </div>
                      )}
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={!isSlackValid(setupData.slack) || testingStates.slack.status === 'testing'}
                      onClick={() => handleTestConnection('slack')}
                      className="text-xs font-semibold h-9 shrink-0 border-blue-200/60 hover:bg-blue-50/50 hover:text-blue-700"
                    >
                      {testingStates.slack.status === 'testing' ? 'Testing...' : 'Test Connection'}
                    </Button>
                  </div>
                )}
              </TabsContent>

              {/* JIRA SETUP TAB */}
              <TabsContent value="jira" className="p-6 focus-visible:outline-none space-y-6">
                <div className="flex items-center justify-between pb-4 border-b border-slate-100">
                  <div className="space-y-0.5">
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-semibold text-slate-800">Jira Ingestion Settings</h3>
                      <button
                        type="button"
                        onClick={() => {
                          setHelpModalTab('jira');
                          setHelpModalOpen(true);
                        }}
                        className="text-[10px] font-extrabold text-blue-600 hover:text-blue-700 bg-blue-50 hover:bg-blue-100/80 px-2 py-0.5 rounded transition-all shrink-0 uppercase tracking-wider"
                      >
                        Setup Guide
                      </button>
                    </div>
                    <p className="text-xs text-slate-500">
                      Link issue trackers and sprints to Lore knowledge vectors.
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    <Label htmlFor="jira-enable" className="text-xs font-bold text-slate-500 uppercase">
                      {setupData.jira.enabled ? 'Enabled' : 'Disabled'}
                    </Label>
                    <Switch
                      id="jira-enable"
                      checked={setupData.jira.enabled}
                      onCheckedChange={() =>
                        updateSetupData({
                          jira: { ...setupData.jira, enabled: !setupData.jira.enabled },
                        })
                      }
                    />
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                  {/* Base URL */}
                  <div className="space-y-1.5 md:col-span-2">
                    <Label htmlFor="j-url" className="text-xs font-semibold text-slate-700">
                      Jira Base URL <span className="text-rose-500">*</span>
                    </Label>
                    <Input
                      id="j-url"
                      value={setupData.jira.baseUrl}
                      onChange={(e) =>
                        updateSetupData({
                          jira: { ...setupData.jira, baseUrl: e.target.value },
                        })
                      }
                      placeholder="e.g. https://acme-tools.atlassian.net"
                      className="text-xs"
                    />
                    {setupData.jira.enabled && setupData.jira.baseUrl && (
                      (() => {
                        try {
                          const url = new URL(setupData.jira.baseUrl);
                          if (url.protocol !== 'http:' && url.protocol !== 'https:') {
                            return (
                              <div className="flex items-center gap-1 text-[11px] text-rose-600 mt-1">
                                <AlertCircle className="w-3 h-3" />
                                <span>URL protocol must be HTTP or HTTPS.</span>
                              </div>
                            );
                          }
                          return null;
                        } catch {
                          return (
                            <div className="flex items-center gap-1 text-[11px] text-rose-600 mt-1">
                              <AlertCircle className="w-3 h-3" />
                              <span>Invalid URL format.</span>
                            </div>
                          );
                        }
                      })()
                    )}
                  </div>

                  {/* Admin Email */}
                  <div className="space-y-1.5">
                    <Label htmlFor="j-email" className="text-xs font-semibold text-slate-700">
                      Jira Administrator Email <span className="text-rose-500">*</span>
                    </Label>
                    <Input
                      id="j-email"
                      type="email"
                      value={setupData.jira.email}
                      onChange={(e) =>
                        updateSetupData({
                          jira: { ...setupData.jira, email: e.target.value },
                        })
                      }
                      placeholder="e.g. cloud-manager@acme.com"
                      className="text-xs"
                    />
                    {setupData.jira.enabled &&
                      setupData.jira.email &&
                      !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(setupData.jira.email) && (
                        <div className="flex items-center gap-1 text-[11px] text-rose-600 mt-1">
                          <AlertCircle className="w-3 h-3" />
                          <span>Invalid email format.</span>
                        </div>
                      )}
                  </div>

                  {/* Project Key */}
                  <div className="space-y-1.5">
                    <Label htmlFor="j-project-key" className="text-xs font-semibold text-slate-700">
                      Project Key <span className="text-slate-400 font-normal">(Optional)</span>
                    </Label>
                    <Input
                      id="j-project-key"
                      value={setupData.jira.projectKey ?? ''}
                      onChange={(e) =>
                        updateSetupData({
                          jira: { ...setupData.jira, projectKey: e.target.value },
                        })
                      }
                      placeholder="e.g. ENG"
                      className="text-xs font-mono"
                    />
                  </div>

                  {/* Auth Type selection */}
                  <div className="space-y-1.5">
                    <Label htmlFor="j-auth-type" className="text-xs font-semibold text-slate-700">
                      Authentication Method
                    </Label>
                    <select
                      id="j-auth-type"
                      value={setupData.jira.authType}
                      onChange={(e) =>
                        updateSetupData({
                          jira: {
                            ...setupData.jira,
                            authType: e.target.value as JiraConfig['authType'],
                          },
                        })
                      }
                      className="w-full h-9 rounded-lg border border-slate-200/70 text-[12px] px-3 bg-white text-slate-800 outline-none focus:ring-2 focus:ring-blue-500/20"
                    >
                      <option value="api_token">Atlassian API Token Authentication</option>
                      <option value="oauth">Standard Enterprise OAuth 2.0</option>
                    </select>
                  </div>

                  {/* Conditional inputs */}
                  {setupData.jira.authType === 'api_token' ? (
                    <div className="space-y-1.5 md:col-span-2">
                      <Label htmlFor="j-api-token" className="text-xs font-semibold text-slate-700">
                        Atlassian API Developer Token <span className="text-rose-500">*</span>
                      </Label>
                      <Input
                        id="j-api-token"
                        type="password"
                        value={setupData.jira.apiToken}
                        onChange={(e) =>
                          updateSetupData({
                            jira: { ...setupData.jira, apiToken: e.target.value },
                          })
                        }
                        placeholder="Paste Atlassian token here..."
                        className="text-xs font-mono"
                      />
                      {setupData.jira.enabled && !setupData.jira.apiToken && (
                        <div className="flex items-center gap-1 text-[11px] text-rose-600 mt-1">
                          <AlertCircle className="w-3 h-3" />
                          <span>API developer token is required for token-based authentication.</span>
                        </div>
                      )}
                    </div>
                  ) : (
                    /* OAuth connection section placeholder */
                    <div className="md:col-span-2 p-5 bg-blue-50/50 border border-blue-150 rounded-xl space-y-3">
                      <div className="flex items-start gap-3">
                        <Plug className="w-5 h-5 text-blue-600 shrink-0 mt-0.5" />
                        <div className="space-y-1">
                          <h4 className="text-xs font-bold text-slate-800">OAuth Connection Required</h4>
                          <p className="text-[11px] text-slate-400 leading-relaxed">
                            OAuth flow authorizes direct securely scoped API handshakes. Connect Atlassian credentials globally on behalf of your org directory.
                          </p>
                        </div>
                      </div>
                      <div className="flex items-center gap-2 pt-1.5">
                        <Button
                          variant="outline"
                          size="sm"
                          className="bg-white hover:bg-slate-50 text-blue-600 hover:text-blue-700 border-blue-200/50 text-[11px] h-8 font-semibold shadow-sm"
                        >
                          <ExternalLink className="w-3.5 h-3.5 mr-1" />
                          Authorize Atlassian Account
                        </Button>
                      </div>
                    </div>
                  )}
                </div>

                {/* Connection Test Section */}
                {setupData.jira.enabled && (
                  <div className="mt-6 pt-5 border-t border-slate-100 flex flex-col sm:flex-row sm:items-center justify-between gap-4 animate-in fade-in duration-300">
                    <div className="flex-1 min-w-0">
                      {testingStates.jira.status === 'success' && (
                        <div className="flex items-start gap-2 text-emerald-600 text-xs">
                          <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5 animate-bounce" />
                          <span className="font-medium">{testingStates.jira.message}</span>
                        </div>
                      )}
                      {testingStates.jira.status === 'failed' && (
                        <div className="flex items-start gap-2 text-rose-600 text-xs">
                          <XCircle className="w-4 h-4 shrink-0 mt-0.5" />
                          <span className="font-medium">{testingStates.jira.message}</span>
                        </div>
                      )}
                      {testingStates.jira.status === 'idle' && (
                        <p className="text-[11px] text-slate-400">
                          Verify Atlassian board integration scopes and developer credentials.
                        </p>
                      )}
                      {testingStates.jira.status === 'testing' && (
                        <div className="flex items-center gap-2 text-blue-600 text-xs font-medium">
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          <span>Pinging Atlassian Cloud REST API...</span>
                        </div>
                      )}
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={!isJiraValid(setupData.jira) || testingStates.jira.status === 'testing'}
                      onClick={() => handleTestConnection('jira')}
                      className="text-xs font-semibold h-9 shrink-0 border-blue-200/60 hover:bg-blue-50/50 hover:text-blue-700"
                    >
                      {testingStates.jira.status === 'testing' ? 'Testing...' : 'Test Connection'}
                    </Button>
                  </div>
                )}
              </TabsContent>
            </Tabs>
          </Card>
        </div>

        {/* Right Column: Connection Summary & Circular Visualization */}
        <div className="space-y-6">
          {/* Connection Summary Panel */}
          <Card className="shadow-sm border-slate-200/70">
            <CardHeader className="pb-4">
              <div className="flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-blue-500" />
                <CardTitle className="text-[14px] font-semibold text-slate-800">
                  Setup Progress
                </CardTitle>
              </div>
              <CardDescription className="text-xs">
                Active global pipeline deployment metrics
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              {/* Progress Ring / Bar representation */}
              <div className="space-y-1.5">
                <div className="flex justify-between text-xs font-semibold">
                  <span className="text-slate-500">Configuration Completion</span>
                  <span className="text-blue-600 font-bold">{completionPercentage}%</span>
                </div>
                <div className="w-full bg-slate-100 rounded-full h-2 overflow-hidden">
                  <div
                    className="bg-gradient-to-r from-blue-500 to-indigo-600 h-full rounded-full transition-all duration-1000 ease-out"
                    style={{ width: `${completionPercentage}%` }}
                  />
                </div>
              </div>

              {/* Ingestion KPI Grid */}
              <div className="grid grid-cols-3 gap-2.5 pt-2">
                <div className="bg-slate-50/70 rounded-lg p-2.5 border border-slate-100 text-center">
                  <div className="text-[9px] font-bold text-slate-400 uppercase tracking-wide">
                    Total
                  </div>
                  <div className="text-lg font-bold text-slate-800 mt-0.5">3</div>
                </div>
                <div className="bg-slate-50/70 rounded-lg p-2.5 border border-slate-100 text-center">
                  <div className="text-[9px] font-bold text-slate-400 uppercase tracking-wide">
                    Draft
                  </div>
                  <div className="text-lg font-bold text-slate-800 mt-0.5">{configuredCount}</div>
                </div>
                <div className="bg-slate-50/70 rounded-lg p-2.5 border border-slate-100 text-center">
                  <div className="text-[9px] font-bold text-emerald-500 uppercase tracking-wide">
                    Ready
                  </div>
                  <div className="text-lg font-bold text-emerald-600 mt-0.5">{readyCount}</div>
                </div>
              </div>

              {/* Status List */}
              <div className="space-y-3 pt-3 border-t border-slate-100">
                <div className="text-xs font-bold text-slate-400 uppercase tracking-wider">
                  Configured Connectors
                </div>
                {integrationsList.map((item) => (
                  <div
                    key={item.id}
                    className="flex items-center justify-between text-xs font-medium text-slate-700"
                  >
                    <div className="flex items-center gap-2">
                      <span
                        className={`w-2 h-2 rounded-full ${
                          item.status === 'Ready'
                            ? 'bg-emerald-500'
                            : item.status === 'Draft Saved'
                            ? 'bg-amber-500'
                            : 'bg-slate-300'
                        }`}
                      />
                      <span>{item.name}</span>
                    </div>
                    <span
                      className={`text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider ${
                        item.status === 'Ready'
                          ? 'bg-emerald-50 text-emerald-700 border border-emerald-100'
                          : item.status === 'Draft Saved'
                          ? 'bg-amber-50 text-amber-700 border border-amber-100'
                          : 'bg-slate-100 text-slate-400 border border-slate-200/50'
                      }`}
                    >
                      {item.status === 'Ready' ? 'Ready' : item.status === 'Draft Saved' ? 'Draft' : 'Empty'}
                    </span>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* Integration Workspace Map Card */}
          <Card className="shadow-sm border-slate-200/70 flex flex-col min-h-[360px] relative overflow-hidden">
            <CardHeader className="border-b border-slate-100 bg-slate-50/50 py-3.5">
              <div className="flex items-center gap-2">
                <Shield className="w-4 h-4 text-slate-500" />
                <CardTitle className="text-xs font-bold text-slate-800 uppercase tracking-wider">
                  Knowledge Brain Map
                </CardTitle>
              </div>
            </CardHeader>
            <CardContent className="flex-1 flex items-center justify-center p-0 relative">
              <div ref={containerRef} className="w-full h-full absolute inset-0">
                {isMobile ? (
                  /* Fallback List visual for small viewports on right-col positioning */
                  <div className="w-full p-4 flex flex-col gap-2">
                    {integrationsList.map((item) => (
                      <div
                        key={item.id}
                        className="p-2.5 rounded-lg border border-slate-100 flex items-center justify-between bg-white text-xs"
                      >
                        <span className="font-semibold text-slate-700">{item.name}</span>
                        <span className={`w-2 h-2 rounded-full ${item.connected ? 'bg-emerald-500' : 'bg-slate-300'}`} />
                      </div>
                    ))}
                  </div>
                ) : (
                  /* Interactive Circular Network Map */
                  <div className="absolute inset-0 w-full h-full scale-[0.85] origin-center">
                    <svg className="absolute inset-0 w-full h-full pointer-events-none">
                      <defs>
                        <filter id="connectionGlow">
                          <feGaussianBlur stdDeviation="3" result="coloredBlur" />
                          <feMerge>
                            <feMergeNode in="coloredBlur" />
                            <feMergeNode in="SourceGraphic" />
                          </feMerge>
                        </filter>
                      </defs>
                      {integrationsList.map((item, index) => {
                        const iconPos = calculateCircularPosition(
                          index,
                          integrationsList.length,
                          iconRadius,
                          brainCenter
                        );
                        return (
                          <ConnectionLine
                            key={item.id}
                            start={iconPos}
                            end={brainCenter}
                            isConnected={item.connected}
                            isHovered={hoveredIntegration === item.id}
                          />
                        );
                      })}
                    </svg>

                    <div
                      className="absolute"
                      style={{
                        left: brainCenter.x,
                        top: brainCenter.y,
                        transform: 'translate(-50%, -50%)',
                        zIndex: 2,
                      }}
                    >
                      <KnowledgeBrain
                        connectedCount={readyCount} // reflects "Ready" integrations
                        className="w-24 h-24 transition-all duration-500"
                      />
                    </div>

                    {integrationsList.map((item, index) => {
                      const iconPos = calculateCircularPosition(
                        index,
                        integrationsList.length,
                        iconRadius,
                        brainCenter
                      );
                      return (
                        <AppIcon
                          key={item.id}
                          logo={item.logo}
                          name={item.name}
                          description={item.description}
                          position={iconPos}
                          connected={item.connected}
                          connecting={false}
                          onClick={item.toggle}
                          onHoverChange={(isHovered) =>
                            setHoveredIntegration(isHovered ? item.id : null)
                          }
                        />
                      );
                    })}
                  </div>
                )}
              </div>
            </CardContent>
          </Card>

          {/* Final Connection Action buttons */}
          <div className="space-y-3 pt-4 border-t border-slate-200/60">
            {savingError && (
              <div className="p-3 rounded-lg bg-rose-50 border border-rose-200/50 text-rose-600 text-xs font-medium flex items-center gap-2 animate-in fade-in">
                <AlertCircle className="w-4 h-4 shrink-0" />
                <span>{savingError}</span>
              </div>
            )}
            
            <div className="flex flex-col sm:flex-row gap-2.5">
              <Button
                type="button"
                onClick={() => setShowSkipConfirm(true)}
                variant="ghost"
                className="flex-1 text-slate-500 hover:text-slate-700 hover:bg-slate-100 text-xs font-bold h-10 border border-transparent"
              >
                Skip Integration
              </Button>
              <Button
                type="button"
                disabled={!hasSuccessfulTest || savingState === 'saving'}
                onClick={handleSaveAndContinue}
                className="flex-[2] bg-blue-600 hover:bg-blue-700 text-white disabled:bg-slate-200 disabled:text-slate-400 text-xs font-bold h-10 shadow-sm relative overflow-hidden transition-all duration-300"
              >
                {savingState === 'saving' ? (
                  <div className="flex items-center justify-center gap-2">
                    <Loader2 className="w-4 h-4 animate-spin" />
                    <span>Saving Org Config...</span>
                  </div>
                ) : (
                  <span>Save & Continue</span>
                )}
              </Button>
            </div>

            <Button
              type="button"
              onClick={handleAutofillDemoData}
              variant="outline"
              size="sm"
              className="w-full text-xs text-blue-600 hover:text-blue-700 hover:bg-blue-50 border-blue-200/50 h-9 font-semibold shadow-sm mt-1"
            >
              <Sparkles className="w-3.5 h-3.5 mr-1.5 text-blue-500" />
              Auto-fill Demo Credentials
            </Button>

            <Button
              onClick={clearDraft}
              variant="outline"
              size="sm"
              className="w-full text-xs text-rose-600 hover:text-rose-700 hover:bg-rose-50 border-rose-200/50 h-9 font-semibold shadow-sm mt-1"
            >
              <Trash2 className="w-3.5 h-3.5 mr-1.5" />
              Reset Workspace Draft
            </Button>
          </div>
        </div>
      </div>

      {showSkipConfirm && (
        <div className="fixed inset-0 bg-slate-900/60 backdrop-blur-sm flex items-center justify-center p-4 z-50 animate-in fade-in duration-200">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-xl border border-slate-100 animate-in zoom-in-95 duration-200">
            <div className="flex items-start gap-4">
              <div className="w-10 h-10 rounded-full bg-rose-50 flex items-center justify-center text-rose-600 shrink-0">
                <AlertCircle className="w-5 h-5" />
              </div>
              <div className="space-y-1.5 flex-1">
                <h3 className="text-base font-bold text-slate-900">Skip Integration Setup?</h3>
                <p className="text-sm text-slate-500 leading-relaxed">
                  Lore requires connection to your data sources to ingest knowledge. Skipping this step means you will have to configure these credentials later in Settings.
                </p>
              </div>
            </div>
            <div className="flex justify-end gap-3 mt-6">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setShowSkipConfirm(false)}
                className="text-xs font-semibold"
              >
                Go Back
              </Button>
              <Button
                onClick={() => {
                  setShowSkipConfirm(false);
                  navigate('/onboarding');
                }}
                className="bg-rose-600 hover:bg-rose-700 text-white text-xs font-semibold shadow-sm"
              >
                Yes, Skip for Now
              </Button>
            </div>
          </div>
        </div>
      )}

      <CredentialHelpModal
        isOpen={helpModalOpen}
        onClose={() => setHelpModalOpen(false)}
        defaultTab={helpModalTab}
      />
    </AppShell>
  );
}
