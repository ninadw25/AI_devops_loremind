import { useEffect, useRef, useState } from 'react';
import { ArrowRight, Phone, Zap, Bot, Globe } from 'lucide-react';
import ContactModal from '@/components/ContactModal';

const features = [
  {
    id: 'on-call',
    icon: Phone,
    title: 'On-call gets the right answers instantly',
    description: 'No more waking up senior engineers at 2 AM. Loremind knows your systems, your past incidents, and your solutions.',
    link: 'Contact us for On-call',
    image: '/images/feature-oncall.jpg',
  },
  {
    id: 'response',
    icon: Zap,
    title: 'Response lets you fix faster, with fewer people',
    description: 'Accelerated by AI and deeply integrated with Slack and Microsoft Teams. Automate workflows and ensure consistent resolution.',
    link: 'Contact us for Response',
    image: '/images/feature-response.jpg',
  },
  {
    id: 'ai-sre',
    icon: Bot,
    title: 'AI SRE resolves issues like your best engineer',
    description: 'From spotting the failing PR to suggesting the fix. AI SRE investigates issues, surfaces next steps, and helps bring your systems back to health.',
    link: 'Contact us for AI SRE',
    image: '/images/feature-aisre.jpg',
  },
  {
    id: 'status-pages',
    icon: Globe,
    title: 'Status Pages keep customers in the loop',
    description: 'Transparent, automated, and effortless. Keep your customers updated, reduce inbound support, and maintain trust when things go wrong.',
    link: 'Contact us for Status Pages',
    image: '/images/feature-status.jpg',
  },
];

export default function Features() {
  const sectionRef = useRef<HTMLDivElement>(null);
  const [visibleItems, setVisibleItems] = useState<Set<number>>(new Set());
  const [showContact, setShowContact] = useState(false);

  useEffect(() => {
    const observers: IntersectionObserver[] = [];
    
    features.forEach((_, index) => {
      const observer = new IntersectionObserver(
        ([entry]) => {
          if (entry.isIntersecting) {
            setVisibleItems((prev) => new Set([...prev, index]));
          }
        },
        { threshold: 0.2 }
      );

      const element = document.getElementById(features[index].id);
      if (element) {
        observer.observe(element);
        observers.push(observer);
      }
    });

    return () => observers.forEach((o) => o.disconnect());
  }, []);

  return (
    <section ref={sectionRef} className="py-24 lg:py-32 bg-white">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        {/* Section Header */}
        <div className="text-center max-w-3xl mx-auto mb-20">
          <h2 className="text-3xl sm:text-4xl lg:text-5xl font-semibold text-slate-900 mb-6">
            Meet the <span className="text-gradient">knowledge command center</span>
          </h2>
          <p className="text-lg text-slate-600">
            From question to answer, give your team everything they need to respond quickly, 
            reduce interruptions, and keep knowledge flowing.
          </p>
        </div>

        {/* Features */}
        <div className="space-y-24 lg:space-y-32">
          {features.map((feature, index) => (
            <div
              id={feature.id}
              key={feature.title}
              className={`grid lg:grid-cols-2 gap-12 lg:gap-16 items-center ${
                index % 2 === 1 ? 'lg:flex-row-reverse' : ''
              }`}
            >
              {/* Image */}
              <div className={`relative transition-all duration-700 ${visibleItems.has(index) ? 'opacity-100 translate-x-0' : index % 2 === 0 ? 'opacity-0 -translate-x-12' : 'opacity-0 translate-x-12'}`}>
                <div className="relative rounded-2xl overflow-hidden shadow-xl border border-slate-200 group">
                  <img 
                    src={feature.image} 
                    alt={feature.title}
                    className="w-full h-auto transition-transform duration-500 group-hover:scale-105"
                  />
                  <div className="absolute inset-0 bg-gradient-to-t from-slate-900/10 to-transparent pointer-events-none" />
                </div>

              </div>

              {/* Content */}
              <div className={`${index % 2 === 1 ? 'lg:order-first' : ''} transition-all duration-700 delay-200 ${visibleItems.has(index) ? 'opacity-100 translate-x-0' : index % 2 === 0 ? 'opacity-0 translate-x-12' : 'opacity-0 -translate-x-12'}`}>
                <div className="w-12 h-12 rounded-xl bg-blue-100 flex items-center justify-center mb-6">
                  <feature.icon className="w-6 h-6 text-blue-600" />
                </div>

                <h3 className="text-2xl sm:text-3xl font-semibold text-slate-900 mb-4">
                  {feature.title}
                </h3>

                <p className="text-lg text-slate-600 mb-6 leading-relaxed">
                  {feature.description}
                </p>

                <button
                  onClick={() => setShowContact(true)}
                  className="inline-flex items-center gap-2 text-blue-600 font-medium hover:text-blue-700 group/link"
                >
                  {feature.link}
                  <ArrowRight className="w-4 h-4 transition-transform group-hover/link:translate-x-1" />
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
      <ContactModal open={showContact} onClose={() => setShowContact(false)} />
    </section>
  );
}
