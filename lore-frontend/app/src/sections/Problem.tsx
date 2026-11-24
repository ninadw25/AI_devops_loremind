import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Clock, Users, TrendingUp } from 'lucide-react';

const stats = [
  { icon: Users, value: 70, suffix: '%', label: 'of organizational knowledge is undocumented' },
  { icon: Clock, value: 20, suffix: '%', label: 'of work time spent searching for information' },
  { icon: TrendingUp, value: 40, suffix: '%', label: 'of employees leave within 2 years at SMBs' },
];

function AnimatedCounter({ value, suffix }: { value: number; suffix: string }) {
  const [count, setCount] = useState(0);
  const counterRef = useRef<HTMLSpanElement>(null);
  const hasAnimated = useRef(false);

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && !hasAnimated.current) {
          hasAnimated.current = true;
          const duration = 2000;
          const steps = 60;
          const increment = value / steps;
          let current = 0;
          
          const timer = setInterval(() => {
            current += increment;
            if (current >= value) {
              setCount(value);
              clearInterval(timer);
            } else {
              setCount(Math.floor(current));
            }
          }, duration / steps);
        }
      },
      { threshold: 0.5 }
    );

    if (counterRef.current) {
      observer.observe(counterRef.current);
    }

    return () => observer.disconnect();
  }, [value]);

  return (
    <span ref={counterRef} className="text-4xl sm:text-5xl font-bold text-blue-700">
      {count}{suffix}
    </span>
  );
}

export default function Problem() {
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
      {/* Background accent */}
      <div className="absolute top-0 right-0 w-1/3 h-full bg-gradient-to-l from-blue-50/50 to-transparent pointer-events-none" />

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="grid lg:grid-cols-2 gap-16 items-center">
          {/* Content */}
          <div>
            <div className={`inline-flex items-center gap-2 px-4 py-2 rounded-full bg-red-100 text-red-700 text-sm font-medium mb-6 transition-all duration-700 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
              <AlertTriangle className="w-4 h-4" />
              <span>The Problem</span>
            </div>

            <h2 className={`text-3xl sm:text-4xl lg:text-5xl font-semibold text-slate-900 leading-tight mb-8 transition-all duration-700 delay-100 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
              <p>Months of progress{' '}</p>
              
              <span className="text-red-600">lost</span>{' '}in minutes
            </h2>

            <div className={`space-y-6 text-lg text-slate-600 leading-relaxed transition-all duration-700 delay-200 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
              <p>
                I worked at a SMB, for 10 months. Built their video generation model from scratch. 
                That model helped crack enterprise deals worth lakhs.
              </p>
              <p>
                When I left, not a single person knew how to run the code. Not a single person 
                understood the decisions behind the architecture.
              </p>
              <p className="text-slate-900 font-medium">
                That's when I realizedcompanies don't lose employees. They lose everything that employee knew.
              </p>
            </div>

            <div className={`mt-8 flex items-center gap-4 transition-all duration-700 delay-300 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
              <div className="w-12 h-12 rounded-full bg-gradient-to-br from-blue-500 to-blue-700 flex items-center justify-center text-white font-semibold">
                PS
              </div>
              <div>
                <div className="font-medium text-slate-900">Arun Gupta</div>
                <div className="text-sm text-slate-500">Head of Engineering</div>
              </div>
            </div>
          </div>

          {/* Stats Grid */}
          <div className={`grid gap-6 transition-all duration-700 delay-400 ${isVisible ? 'opacity-100 translate-x-0' : 'opacity-0 translate-x-8'}`}>
            {stats.map((stat, index) => (
              <div 
                key={stat.label}
                className="bg-slate-50 rounded-2xl p-6 lg:p-8 hover-lift border border-slate-100"
                style={{ animationDelay: `${index * 100}ms` }}
              >
                <div className="flex items-start gap-4">
                  <div className="w-12 h-12 rounded-xl bg-blue-100 flex items-center justify-center flex-shrink-0">
                    <stat.icon className="w-6 h-6 text-blue-600" />
                  </div>
                  <div>
                    <AnimatedCounter value={stat.value} suffix={stat.suffix} />
                    <p className="text-slate-600 mt-2">{stat.label}</p>
                  </div>
                </div>
              </div>
            ))}

            {/* Knowledge loss visualization */}
            <div className="bg-gradient-to-br from-red-50 to-orange-50 rounded-2xl p-6 lg:p-8 border border-red-100">
              <div className="flex items-center gap-3 mb-4">
                <div className="w-10 h-10 rounded-lg bg-red-100 flex items-center justify-center">
                  <AlertTriangle className="w-5 h-5 text-red-600" />
                </div>
                <span className="font-semibold text-red-800">Major Knowledge Drain in SMBs</span>
              </div>
              <div className="space-y-3">
                <div className="flex items-center gap-3">
                  <div className="w-2 h-2 rounded-full bg-red-500" />
                  <span className="text-slate-700">Average employee holds ₹15-30 lakhs worth of institutional knowledge</span>
                </div>
                <div className="flex items-center gap-3">
                  <div className="w-2 h-2 rounded-full bg-red-500" />
                  <span className="text-slate-700">90% of decisions never get documented</span>
                </div>
                <div className="flex items-center gap-3">
                  <div className="w-2 h-2 rounded-full bg-red-500" />
                  <span className="text-slate-700">Critical context buried in 10,000+ messages</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
