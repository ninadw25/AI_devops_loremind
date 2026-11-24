import { useEffect, useRef, useState } from 'react';
import { Check, X, ArrowRight } from 'lucide-react';

const expectations = [
  'Knowledge is in our docs',
  'We use Slack/Notion to track decisions',
  'People will do knowledge transfer',
];

const realities = [
  '90% of decisions never get documented',
  'Critical context buried in 10,000 messages',
  '3 weeks of KT can\'t capture years of experience',
];

export default function ExpectationReality() {
  const sectionRef = useRef<HTMLDivElement>(null);
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsVisible(true);
        }
      },
      { threshold: 0.2 }
    );

    if (sectionRef.current) {
      observer.observe(sectionRef.current);
    }

    return () => observer.disconnect();
  }, []);

  return (
    <section 
      ref={sectionRef}
      className="py-24 lg:py-32 bg-slate-50 relative overflow-hidden"
    >
      {/* Background pattern */}
      <div className="absolute inset-0 opacity-30">
        <div className="absolute inset-0" style={{
          backgroundImage: 'radial-gradient(circle at 1px 1px, #cbd5e1 1px, transparent 0)',
          backgroundSize: '40px 40px'
        }} />
      </div>

      <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 relative">
        {/* Section Title */}
        <div className={`text-center mb-16 transition-all duration-700 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
          <h2 className="text-3xl sm:text-4xl lg:text-5xl font-semibold text-slate-900">
            Expectation vs <span className="text-gradient">Reality</span>
          </h2>
        </div>

        {/* Comparison Grid */}
        <div className="grid md:grid-cols-2 gap-8 lg:gap-16">
          {/* Expectations */}
          <div className={`transition-all duration-700 delay-200 ${isVisible ? 'opacity-100 translate-x-0' : 'opacity-0 -translate-x-8'}`}>
            <div className="bg-white rounded-2xl p-8 shadow-lg border border-slate-200 h-full">
              <h3 className="text-xl font-semibold text-slate-500 mb-6 flex items-center gap-2">
                What Companies Think
              </h3>
              <div className="space-y-4">
                {expectations.map((item, index) => (
                  <div 
                    key={item}
                    className="flex items-center gap-4 p-4 rounded-xl bg-green-50/50 border border-green-100"
                    style={{ animationDelay: `${index * 100}ms` }}
                  >
                    <div className="w-8 h-8 rounded-full bg-green-100 flex items-center justify-center flex-shrink-0">
                      <Check className="w-4 h-4 text-green-600" />
                    </div>
                    <span className="text-slate-600">{item}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Divider Arrow */}
          <div className="hidden md:flex absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-10">
            <div className={`w-12 h-12 rounded-full bg-blue-600 flex items-center justify-center shadow-lg transition-all duration-700 delay-300 ${isVisible ? 'opacity-100 scale-100' : 'opacity-0 scale-0'}`}>
              <ArrowRight className="w-6 h-6 text-white" />
            </div>
          </div>

          {/* Realities */}
          <div className={`transition-all duration-700 delay-400 ${isVisible ? 'opacity-100 translate-x-0' : 'opacity-0 translate-x-8'}`}>
            <div className="bg-white rounded-2xl p-8 shadow-lg border-2 border-red-100 h-full">
              <h3 className="text-xl font-semibold text-slate-900 mb-6 flex items-center gap-2">
                What Actually Happens
              </h3>
              <div className="space-y-4">
                {realities.map((item, index) => (
                  <div 
                    key={item}
                    className="flex items-center gap-4 p-4 rounded-xl bg-red-50 border border-red-100"
                    style={{ animationDelay: `${(index + 3) * 100}ms` }}
                  >
                    <div className="w-8 h-8 rounded-full bg-red-100 flex items-center justify-center flex-shrink-0">
                      <X className="w-4 h-4 text-red-600" />
                    </div>
                    <span className="text-slate-700 font-medium">{item}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Bottom stat */}
        <div className={`mt-12 text-center transition-all duration-700 delay-600 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
          <div className="inline-flex items-center gap-3 px-6 py-3 rounded-full bg-red-100 text-red-800">
            <AlertTriangle className="w-5 h-5" />
            <span className="font-semibold">Major Knowledge Drain in SMBs</span>
          </div>
        </div>
      </div>
    </section>
  );
}

// Need to import this
import { AlertTriangle } from 'lucide-react';
