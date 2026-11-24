import { useState, useEffect } from 'react';
import { X, Key, AlertCircle, CheckCircle2 } from 'lucide-react';

const STORAGE_KEY = 'lore_api_settings';

export interface ApiSettings {
  apiKey: string;
  model: string;
  provider: 'openai' | 'openrouter';
}

const MODEL_OPTIONS = [
  { value: 'gpt-4o-mini', label: 'GPT-4o Mini' },
  { value: 'gpt-4o', label: 'GPT-4o' },
  { value: 'gpt-4.1-mini', label: 'GPT-4.1 Mini' },
  { value: 'gpt-4.1', label: 'GPT-4.1' },
  { value: 'o4-mini', label: 'o4 Mini' },
];

export function loadApiSettings(): ApiSettings | null {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return parsed.apiKey ? parsed : null;
  } catch {
    return null;
  }
}

export function saveApiSettings(settings: ApiSettings | null) {
  if (settings && settings.apiKey) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } else {
    localStorage.removeItem(STORAGE_KEY);
  }
}

interface ApiKeyModalProps {
  open: boolean;
  onClose: () => void;
  quota: { used: number; limit: number; remaining: number } | null;
}

export default function ApiKeyModal({ open, onClose, quota }: ApiKeyModalProps) {
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState('');
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (open) {
      const existing = loadApiSettings();
      if (existing) {
        setApiKey(existing.apiKey);
        setModel(existing.model);
      }
      setSaved(false);
    }
  }, [open]);

  const handleSave = () => {
    if (apiKey.trim()) {
      saveApiSettings({ apiKey: apiKey.trim(), model, provider: 'openai' });
    } else {
      saveApiSettings(null);
    }
    setSaved(true);
    setTimeout(() => onClose(), 800);
  };

  const handleClear = () => {
    setApiKey('');
    setModel('');
    saveApiSettings(null);
    setSaved(false);
  };

  if (!open) return null;

  const hasKey = loadApiSettings()?.apiKey;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-2xl shadow-xl border border-slate-200 w-full max-w-md mx-4">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100">
          <div className="flex items-center gap-2">
            <Key className="w-5 h-5 text-blue-600" />
            <h2 className="font-semibold text-slate-900">API Settings</h2>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-5 space-y-4">
          {/* Quota info */}
          {quota && !hasKey && (
            <div className={`flex items-start gap-2 text-sm px-3 py-2.5 rounded-lg ${
              quota.remaining > 0
                ? 'bg-blue-50 text-blue-700'
                : 'bg-amber-50 text-amber-700'
            }`}>
              <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
              <div>
                {quota.remaining > 0
                  ? `You have ${quota.remaining} of ${quota.limit} free queries remaining.`
                  : 'Free queries exhausted. Add your API key below to continue.'}
              </div>
            </div>
          )}

          {hasKey && (
            <div className="flex items-center gap-2 text-sm px-3 py-2.5 rounded-lg bg-green-50 text-green-700">
              <CheckCircle2 className="w-4 h-4 flex-shrink-0" />
              Using your own API key - unlimited queries.
            </div>
          )}

          {/* Provider hint */}
          <p className="text-xs text-slate-500">
            Enter your OpenAI API key (sk-...). Your key is stored in your browser only and sent per-request - never saved on our servers.
          </p>

          {/* API Key */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">API Key</label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => { setApiKey(e.target.value); setSaved(false); }}
              placeholder="sk-... or sk-or-..."
              className="w-full px-4 py-2.5 rounded-xl bg-slate-50 border border-slate-200 text-slate-900 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-300 transition-all text-sm font-mono"
            />
          </div>

          {/* Model */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">Model</label>
            <select
              value={model}
              onChange={(e) => { setModel(e.target.value); setSaved(false); }}
              className="w-full px-4 py-2.5 rounded-xl bg-slate-50 border border-slate-200 text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-300 transition-all text-sm"
            >
              {MODEL_OPTIONS.map(opt => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-6 py-4 border-t border-slate-100">
          <button
            onClick={handleClear}
            className="text-sm text-slate-500 hover:text-red-600 transition-colors"
          >
            Clear key
          </button>
          <button
            onClick={handleSave}
            className={`px-5 py-2 rounded-xl text-sm font-medium text-white transition-all ${
              saved ? 'bg-green-500' : 'bg-blue-600 hover:bg-blue-700'
            }`}
          >
            {saved ? 'Saved!' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}
