import { useState } from 'react';
import { createPortal } from 'react-dom';
import { Button } from '@/components/ui/button';
import { X, Send, CheckCircle, AlertCircle } from 'lucide-react';

const CHANNELS = ['Gmail', 'Slack', 'Notion', 'Jira', 'GitHub', 'MS Teams', 'Other'] as const;

interface FormData {
  companyName: string;
  companyUrl: string;
  email: string;
  phone: string;
  domain: string;
  teamSize: string;
  channels: string[];
  otherChannel: string;
  message: string;
}

const emptyForm: FormData = {
  companyName: '',
  companyUrl: '',
  email: '',
  phone: '',
  domain: '',
  teamSize: '',
  channels: [],
  otherChannel: '',
  message: '',
};

interface ContactModalProps {
  open: boolean;
  onClose: () => void;
}

export default function ContactModal({ open, onClose }: ContactModalProps) {
  const [form, setForm] = useState<FormData>(emptyForm);
  const [status, setStatus] = useState<'idle' | 'sending' | 'sent' | 'error'>('idle');

  if (!open) return null;

  const toggleChannel = (ch: string) => {
    setForm((f) => ({
      ...f,
      channels: f.channels.includes(ch)
        ? f.channels.filter((c) => c !== ch)
        : [...f.channels, ch],
    }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setStatus('sending');

    try {
      const allChannels = [...form.channels];
      if (form.channels.includes('Other') && form.otherChannel.trim()) {
        allChannels.push(form.otherChannel.trim());
      }

      // Using Web3Forms free tier - replace YOUR_ACCESS_KEY with your key from https://web3forms.com
      const res = await fetch('https://api.web3forms.com/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          access_key: '62269e4e-2306-4c2a-95af-70a379410fc9',
          subject: `Lore Contact: ${form.companyName}`,
          company_name: form.companyName,
          company_url: form.companyUrl,
          email: form.email,
          phone: form.phone || 'Not provided',
          company_domain: form.domain,
          team_size: form.teamSize || 'Not provided',
          communication_channels: allChannels.join(', ') || 'None selected',
          additional_query: form.message || 'None',
        }),
      });

      const data = await res.json();
      if (data.success) {
        setStatus('sent');
        setForm(emptyForm);
      } else {
        console.error('Web3Forms error:', data);
        setStatus('error');
      }
    } catch (err) {
      console.error('Submit error:', err);
      setStatus('error');
    }
  };

  const handleClose = () => {
    setStatus('idle');
    onClose();
  };

  const inputClass =
    'w-full px-4 py-2.5 rounded-lg border border-slate-300 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 outline-none transition-all text-slate-900';

  return createPortal(
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      onClick={handleClose}
    >
      <div
        className="relative w-full max-w-lg bg-white rounded-2xl shadow-2xl p-8 max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          onClick={handleClose}
          className="absolute top-4 right-4 w-8 h-8 rounded-full bg-slate-100 hover:bg-slate-200 flex items-center justify-center text-slate-500 transition-colors"
        >
          <X className="w-4 h-4" />
        </button>

        {status === 'sent' ? (
          <div className="text-center py-8">
            <CheckCircle className="w-16 h-16 text-green-500 mx-auto mb-4" />
            <h3 className="text-2xl font-semibold text-slate-900 mb-2">We've got your details!</h3>
            <p className="text-slate-600 mb-6">Our team will reach out to you shortly.</p>
            <Button onClick={handleClose} className="bg-blue-700 hover:bg-blue-800 text-white rounded-full px-8">
              Close
            </Button>
          </div>
        ) : (
          <>
            <h3 className="text-2xl font-semibold text-slate-900 mb-1">Get in touch</h3>
            <p className="text-slate-500 mb-1">Tell us about your company and we'll show you how Lore can help.</p>
            <p className="text-slate-500 text-sm mb-6">Or email us directly at <a href="mailto:founders@loremind.in" className="text-blue-600 hover:underline">founders@loremind.in</a></p>

            {status === 'error' && (
              <div className="flex items-center gap-2 text-red-600 bg-red-50 rounded-lg p-3 mb-4 text-sm">
                <AlertCircle className="w-4 h-4 flex-shrink-0" />
                Something went wrong. Please try again.
              </div>
            )}

            <form onSubmit={handleSubmit} className="space-y-4">
              {/* Company Name */}
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Company name *</label>
                <input
                  type="text"
                  required
                  value={form.companyName}
                  onChange={(e) => setForm({ ...form, companyName: e.target.value })}
                  className={inputClass}
                />
              </div>

              {/* Company URL */}
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Company website *</label>
                <input
                  type="text"
                  required
                  value={form.companyUrl}
                  onChange={(e) => setForm({ ...form, companyUrl: e.target.value })}
                  className={inputClass}
                />
              </div>

              {/* Email & Phone side by side */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-1">Contact email *</label>
                  <input
                    type="email"
                    required
                    value={form.email}
                    onChange={(e) => setForm({ ...form, email: e.target.value })}
                    className={inputClass}
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-1">
                    Phone <span className="text-slate-400 font-normal">(optional)</span>
                  </label>
                  <input
                    type="tel"
                    value={form.phone}
                    onChange={(e) => setForm({ ...form, phone: e.target.value })}
                    className={inputClass}
                  />
                </div>
              </div>

              {/* Company Domain */}
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">What industry is your company in? *</label>
                <select
                  required
                  value={form.domain}
                  onChange={(e) => setForm({ ...form, domain: e.target.value })}
                  className={`${inputClass} ${!form.domain ? 'text-slate-400' : ''}`}
                >
                  <option value="" disabled>Select your industry</option>
                  <option value="Software / IT">Software / IT</option>
                  <option value="Hardware / Manufacturing">Hardware / Manufacturing</option>
                  <option value="Retail / E-commerce">Retail / E-commerce</option>
                  <option value="Finance / Banking">Finance / Banking</option>
                  <option value="Healthcare">Healthcare</option>
                  <option value="Education">Education</option>
                  <option value="Consulting / Services">Consulting / Services</option>
                  <option value="Legal">Legal</option>
                  <option value="Media / Marketing">Media / Marketing</option>
                  <option value="Other">Other</option>
                </select>
              </div>

              {/* Team Size */}
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  Team size <span className="text-slate-400 font-normal">(optional)</span>
                </label>
                <select
                  value={form.teamSize}
                  onChange={(e) => setForm({ ...form, teamSize: e.target.value })}
                  className={`${inputClass} ${!form.teamSize ? 'text-slate-400' : ''}`}
                >
                  <option value="" disabled>Number of employees</option>
                  <option value="1-10">1 - 10</option>
                  <option value="11-50">11 - 50</option>
                  <option value="51-200">51 - 200</option>
                  <option value="201-500">201 - 500</option>
                  <option value="500+">500+</option>
                </select>
              </div>

              {/* Communication Channels */}
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-2">What communication channels does your team use? *</label>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                  {CHANNELS.map((ch) => (
                    <label
                      key={ch}
                      className={`flex items-center gap-2 px-3 py-2.5 rounded-lg border cursor-pointer transition-all text-sm ${
                        form.channels.includes(ch)
                          ? 'border-blue-500 bg-blue-50 text-blue-700'
                          : 'border-slate-200 hover:border-slate-300 text-slate-700'
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={form.channels.includes(ch)}
                        onChange={() => toggleChannel(ch)}
                        className="sr-only"
                      />
                      <div
                        className={`w-4 h-4 rounded border flex items-center justify-center flex-shrink-0 ${
                          form.channels.includes(ch)
                            ? 'bg-blue-600 border-blue-600'
                            : 'border-slate-300'
                        }`}
                      >
                        {form.channels.includes(ch) && (
                          <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                          </svg>
                        )}
                      </div>
                      {ch}
                    </label>
                  ))}
                </div>
                {form.channels.includes('Other') && (
                  <input
                    type="text"
                    value={form.otherChannel}
                    onChange={(e) => setForm({ ...form, otherChannel: e.target.value })}
                    className={`${inputClass} mt-2`}
                  />
                )}
              </div>

              {/* Additional Query */}
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  Anything else you'd like us to know? <span className="text-slate-400 font-normal">(optional)</span>
                </label>
                <textarea
                  rows={3}
                  value={form.message}
                  onChange={(e) => setForm({ ...form, message: e.target.value })}
                  className={`${inputClass} resize-none`}
                  placeholder="Team size, specific pain points, questions..."
                />
              </div>

              <Button
                type="submit"
                disabled={status === 'sending'}
                className="w-full bg-blue-700 hover:bg-blue-800 text-white rounded-full py-6 text-base font-medium"
              >
                {status === 'sending' ? 'Sending...' : 'Submit'}
                {status !== 'sending' && <Send className="w-4 h-4 ml-2" />}
              </Button>
            </form>
          </>
        )}
      </div>
    </div>,
    document.body
  );
}
