import { useEffect, useRef, useState } from 'react';
import { Plug, Brain, MessageCircle, Cpu } from 'lucide-react';

const steps = [
  {
    number: '01',
    icon: Plug,
    title: 'Connect',
    description: 'We plug into tools your team already usesSlack, GitHub, Notion, Jira, and more.',
  },
  {
    number: '02',
    icon: Brain,
    title: 'Passive Learning',
    description: 'Our AI reads, understands, and maps relationships. No new app to learn. No behavior change.',
  },
  {
    number: '03',
    icon: MessageCircle,
    title: 'Natural Language',
    description: 'Ask questions in plain English. Get answers sourced from actual company conversations.',
  },
  {
    number: '04',
    icon: Cpu,
    title: 'LORE Engine',
    description: 'Like a tutor learning from a teacher, Lore understands from company functioning.',
  },
];

export default function HowItWorks() {
  const sectionRef = useRef<HTMLDivElement>(null);
  const [isVisible, setIsVisible] = useState(false);
  const [activeStep, setActiveStep] = useState(0);

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

  // Auto-advance steps
  useEffect(() => {
    if (!isVisible) return;
    const interval = setInterval(() => {
      setActiveStep((prev) => (prev + 1) % steps.length);
    }, 3000);
    return () => clearInterval(interval);
  }, [isVisible]);

  return (
    <section 
      ref={sectionRef}
      className="py-24 lg:py-32 bg-slate-50 relative overflow-hidden"
    >
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        {/* Header */}
        <div className="text-center max-w-3xl mx-auto mb-16">
          <h2 className={`text-3xl sm:text-4xl lg:text-5xl font-semibold text-slate-900 mb-6 transition-all duration-700 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
            How We <span className="text-gradient">Solve It</span>
          </h2>
          <p className={`text-lg text-slate-600 transition-all duration-700 delay-100 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
            Four simple steps to capture and preserve your company's knowledge
          </p>
        </div>

        {/* Steps - Desktop */}
        <div className="hidden lg:block">
          <div className="relative">
            {/* Connecting line */}
            <div className="absolute top-24 left-0 right-0 h-1 bg-slate-200">
              <div 
                className="h-full bg-gradient-to-r from-blue-600 to-blue-400 transition-all duration-500"
                style={{ width: `${((activeStep + 1) / steps.length) * 100}%` }}
              />
            </div>

            {/* Steps */}
            <div className="grid grid-cols-4 gap-8">
              {steps.map((step, index) => (
                <div
                  key={step.number}
                  className={`relative text-center transition-all duration-500 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-8'}`}
                  style={{ transitionDelay: `${index * 150}ms` }}
                  onMouseEnter={() => setActiveStep(index)}
                >
                  {/* Node */}
                  <div className={`
                    relative z-10 w-12 h-12 mx-auto rounded-full flex items-center justify-center mb-6
                    transition-all duration-300 cursor-pointer
                    ${activeStep === index 
                      ? 'bg-blue-600 text-white scale-110 shadow-lg shadow-blue-600/30' 
                      : 'bg-white text-slate-400 border-2 border-slate-200 hover:border-blue-300'
                    }
                  `}>
                    <step.icon className="w-5 h-5" />
                  </div>

                  {/* Number */}
                  <div className={`text-sm font-medium mb-2 ${activeStep === index ? 'text-blue-600' : 'text-slate-400'}`}>
                    {step.number}
                  </div>

                  {/* Title */}
                  <h3 className={`text-lg font-semibold mb-2 transition-colors duration-300 ${activeStep === index ? 'text-slate-900' : 'text-slate-600'}`}>
                    {step.title}
                  </h3>

                  {/* Description */}
                  <p className={`text-sm leading-relaxed transition-all duration-300 ${activeStep === index ? 'text-slate-600 opacity-100' : 'text-slate-400 opacity-70'}`}>
                    {step.description}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Steps - Mobile */}
        <div className="lg:hidden space-y-6">
          {steps.map((step, index) => (
            <div
              key={step.number}
              className={`flex items-start gap-4 p-6 bg-white rounded-xl border border-slate-200 transition-all duration-500 ${isVisible ? 'opacity-100 translate-x-0' : 'opacity-0 -translate-x-8'}`}
              style={{ transitionDelay: `${index * 100}ms` }}
            >
              <div className="w-12 h-12 rounded-full bg-blue-100 flex items-center justify-center flex-shrink-0">
                <step.icon className="w-5 h-5 text-blue-600" />
              </div>
              <div>
                <div className="text-sm font-medium text-blue-600 mb-1">{step.number}</div>
                <h3 className="text-lg font-semibold text-slate-900 mb-2">{step.title}</h3>
                <p className="text-slate-600 text-sm">{step.description}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
