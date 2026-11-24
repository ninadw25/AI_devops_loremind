import { useEffect, useRef, useState } from 'react';
import { Check, Server, Cloud, Mail } from 'lucide-react';
import { Button } from '@/components/ui/button';
import ContactModal from '@/components/ContactModal';

const supportTiers = [
  { name: 'Tier 1', response: 'Response within 24 hours', fix: 'Fix in 2-3 working days' },
  { name: 'Tier 2', response: 'Response within 4 hours', fix: 'Fix in 1 working day' },
  { name: 'Tier 3', response: 'Response within 2 hours', fix: 'Fix in 12 working hours' },
];

export default function Pricing() {
  const sectionRef = useRef<HTMLDivElement>(null);
  const [isVisible, setIsVisible] = useState(false);
  const [showContact, setShowContact] = useState(false);

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsVisible(true);
        }
      },
      { threshold: 0.1 }
    );

    if (sectionRef.current) {
      observer.observe(sectionRef.current);
    }

    return () => observer.disconnect();
  }, []);

  return (
    <section
      id="pricing"
      ref={sectionRef}
      className="py-24 lg:py-32 bg-white relative overflow-hidden"
    >
      {/* Background accents */}
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[800px] h-[800px] rounded-full bg-blue-50/50 blur-3xl -z-10" />

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        {/* Header */}
        <div className={`text-center max-w-3xl mx-auto mb-16 transition-all duration-700 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
          <h2 className="text-3xl sm:text-4xl lg:text-5xl font-semibold text-slate-900 mb-4">
            Simple, <span className="text-gradient">transparent</span> pricing
          </h2>
          <p className="text-lg text-slate-600">
            Choose the deployment model that works for your team. No hidden fees.
          </p>
        </div>

        {/* Pricing Cards */}
        <div className="grid md:grid-cols-2 gap-8 max-w-5xl mx-auto">
          {/* Model 1 - On-Premise */}
          <div
            className={`relative rounded-2xl border border-slate-200 bg-white p-8 lg:p-10 transition-all duration-700 delay-100 hover:shadow-xl hover:-translate-y-1 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-8'}`}
          >
            <div className="w-12 h-12 rounded-xl bg-slate-100 flex items-center justify-center mb-6">
              <Server className="w-6 h-6 text-slate-700" />
            </div>

            <div className="mb-6">
              <h3 className="text-xl font-semibold text-slate-900 mb-1">On-Premise</h3>
              <p className="text-slate-500 text-sm">Your server. Your LLM API key.</p>
            </div>

            <div className="flex items-baseline gap-1 mb-8">
              <span className="text-5xl font-bold text-slate-900">$4</span>
              <span className="text-slate-500">/ working employee / month</span>
            </div>

            <div className="space-y-3 mb-8">
              <div className="flex items-center gap-3">
                <Check className="w-5 h-5 text-green-500 flex-shrink-0" />
                <span className="text-slate-700">Full data ownership</span>
              </div>
              <div className="flex items-center gap-3">
                <Check className="w-5 h-5 text-green-500 flex-shrink-0" />
                <span className="text-slate-700">Use your own LLM API key</span>
              </div>
              <div className="flex items-center gap-3">
                <Check className="w-5 h-5 text-green-500 flex-shrink-0" />
                <span className="text-slate-700">Deploy on your infrastructure</span>
              </div>
              <div className="flex items-center gap-3">
                <Check className="w-5 h-5 text-green-500 flex-shrink-0" />
                <span className="text-slate-700">All integrations included</span>
              </div>
            </div>

            {/* Support Tiers */}
            <div className="border-t border-slate-200 pt-6 mb-8">
              <p className="text-sm font-medium text-slate-900 mb-4">Support tiers <span className="text-slate-400 font-normal">(additional charge)</span></p>
              <div className="space-y-3">
                {supportTiers.map((tier) => (
                  <div key={tier.name} className="flex items-start gap-3 text-sm">
                    <span className="inline-flex items-center justify-center w-16 flex-shrink-0 rounded-full bg-blue-50 text-blue-700 font-medium py-0.5 text-xs">
                      {tier.name}
                    </span>
                    <div className="text-slate-600">
                      {tier.response} · {tier.fix}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <Button
              onClick={() => setShowContact(true)}
              className="w-full rounded-full py-6 text-base font-medium border-2 border-slate-900 bg-white text-slate-900 hover:bg-slate-900 hover:text-white transition-colors"
            >
              Get started
              <Mail className="w-4 h-4 ml-2" />
            </Button>
          </div>

          {/* Model 2 - Subscription */}
          <div
            className={`relative rounded-2xl border-2 border-blue-600 bg-gradient-to-b from-blue-600 to-blue-800 p-8 lg:p-10 transition-all duration-700 delay-200 hover:shadow-xl hover:-translate-y-1 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-8'}`}
          >
            {/* Popular badge */}
            <div className="absolute -top-4 left-1/2 -translate-x-1/2">
              <span className="bg-amber-400 text-amber-900 text-xs font-bold uppercase tracking-wider px-4 py-1.5 rounded-full shadow-lg">
                Most Popular
              </span>
            </div>

            <div className="w-12 h-12 rounded-xl bg-white/20 flex items-center justify-center mb-6">
              <Cloud className="w-6 h-6 text-white" />
            </div>

            <div className="mb-6">
              <h3 className="text-xl font-semibold text-white mb-1">Subscription</h3>
              <p className="text-blue-200 text-sm">AWS hosting & LLM services fully managed by us.</p>
            </div>

            <div className="flex items-baseline gap-1 mb-8">
              <span className="text-5xl font-bold text-white">$6</span>
              <span className="text-blue-200">/ working employee / month</span>
            </div>

            <div className="space-y-3 mb-8">
              <div className="flex items-center gap-3">
                <Check className="w-5 h-5 text-green-300 flex-shrink-0" />
                <span className="text-blue-50">Everything in On-Premise</span>
              </div>
              <div className="flex items-center gap-3">
                <Check className="w-5 h-5 text-green-300 flex-shrink-0" />
                <span className="text-blue-50">Fully managed AWS hosting</span>
              </div>
              <div className="flex items-center gap-3">
                <Check className="w-5 h-5 text-green-300 flex-shrink-0" />
                <span className="text-blue-50">LLM costs included</span>
              </div>
              <div className="flex items-center gap-3">
                <Check className="w-5 h-5 text-green-300 flex-shrink-0" />
                <span className="text-blue-50">Zero setup - we handle everything</span>
              </div>
              <div className="flex items-center gap-3">
                <Check className="w-5 h-5 text-green-300 flex-shrink-0" />
                <span className="text-blue-50">Automatic updates & scaling</span>
              </div>
              <div className="flex items-center gap-3">
                <Check className="w-5 h-5 text-green-300 flex-shrink-0" />
                <span className="text-blue-50">Priority support included</span>
              </div>
            </div>

            <Button
              onClick={() => setShowContact(true)}
              className="w-full rounded-full py-6 text-base font-medium bg-white text-blue-700 hover:bg-blue-50 transition-colors shadow-lg"
            >
              Get started
              <Mail className="w-4 h-4 ml-2" />
            </Button>
          </div>
        </div>
      </div>

      <ContactModal open={showContact} onClose={() => setShowContact(false)} />
    </section>
  );
}
