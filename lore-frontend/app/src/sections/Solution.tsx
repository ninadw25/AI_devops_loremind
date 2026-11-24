import { useEffect, useRef, useState } from 'react';
import { Zap, Shield, Database, Sparkles } from 'lucide-react';

const features = [
  {
    icon: Zap,
    title: 'Daily Value',
    description: 'Stop interrupting your senior dev. Stop digging through 50 email threads. Get instant answers sourced from actual company conversations.',
    color: 'amber',
  },
  {
    icon: Shield,
    title: 'Coverage During Leaves',
    description: "Access a team member's project status, pending decisions, and client context without calling them on their day off.",
    color: 'blue',
  },
  {
    icon: Database,
    title: 'Long Term Value',
    description: 'When someone leaves, nothing is lost. Their knowledge stays queryable, their decisions documented, their context preserved.',
    color: 'green',
  },
];

export default function Solution() {
  const sectionRef = useRef<HTMLDivElement>(null);
  const [isVisible, setIsVisible] = useState(false);

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
      ref={sectionRef}
      className="py-24 lg:py-32 bg-white relative overflow-hidden"
    >
      {/* Background gradient */}
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] rounded-full bg-blue-50/50 blur-3xl pointer-events-none" />

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 relative">
        {/* Header */}
        <div className="text-center max-w-3xl mx-auto mb-16">
          <div className={`inline-flex items-center gap-2 px-4 py-2 rounded-full bg-blue-100 text-blue-700 text-sm font-medium mb-6 transition-all duration-700 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
            <Sparkles className="w-4 h-4" />
            <span>The Solution</span>
          </div>

          <h2 className={`text-3xl sm:text-4xl lg:text-5xl font-semibold text-slate-900 mb-6 transition-all duration-700 delay-100 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
            The Organizational <span className="text-gradient">Brain</span>
          </h2>

          <p className={`text-lg text-slate-600 transition-all duration-700 delay-200 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
            With Loremind, the knowledge stays. Every conversation, every code decision, 
            every client relationshippreserved and queryable.
          </p>
        </div>

        {/* Feature Cards */}
        <div className="grid md:grid-cols-3 gap-8">
          {features.map((feature, index) => (
            <div
              key={feature.title}
              className={`group relative bg-white rounded-2xl p-8 border border-slate-200 shadow-sm hover:shadow-xl transition-all duration-500 hover:-translate-y-2 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-8'}`}
              style={{ transitionDelay: `${(index + 3) * 100}ms` }}
            >
              {/* Icon */}
              <div className={`w-14 h-14 rounded-xl flex items-center justify-center mb-6 transition-transform duration-300 group-hover:scale-110 group-hover:rotate-3 ${
                feature.color === 'amber' ? 'bg-amber-100' :
                feature.color === 'blue' ? 'bg-blue-100' :
                'bg-green-100'
              }`}>
                <feature.icon className={`w-7 h-7 ${
                  feature.color === 'amber' ? 'text-amber-600' :
                  feature.color === 'blue' ? 'text-blue-600' :
                  'text-green-600'
                }`} />
              </div>

              {/* Content */}
              <h3 className="text-xl font-semibold text-slate-900 mb-3">
                {feature.title}
              </h3>
              <p className="text-slate-600 leading-relaxed">
                {feature.description}
              </p>

              {/* Hover border effect */}
              <div className={`absolute inset-0 rounded-2xl border-2 opacity-0 group-hover:opacity-100 transition-opacity duration-300 ${
                feature.color === 'amber' ? 'border-amber-300' :
                feature.color === 'blue' ? 'border-blue-300' :
                'border-green-300'
              }`} />
            </div>
          ))}
        </div>

        {/* Bottom tagline */}
        <div className={`mt-16 text-center transition-all duration-700 delay-600 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
          <p className="text-2xl font-medium text-slate-900">
            Nothing leaves when people do.
          </p>
        </div>
      </div>
    </section>
  );
}
