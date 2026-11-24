import { useEffect, useRef, useState } from 'react';
import { BookOpen, GitBranch, BarChart3, Puzzle, Sparkles, ArrowRight } from 'lucide-react';

const foundations = [
  {
    icon: BookOpen,
    title: 'Catalog',
    description: 'Put the right context at your team\'s fingertips, instantly.',
    size: 'normal',
  },
  {
    icon: GitBranch,
    title: 'Workflows',
    description: 'Automate knowledge processes and stay consistent at scale.',
    size: 'normal',
  },
  {
    icon: BarChart3,
    title: 'Insights',
    description: 'Understand trends, reduce noise, and improve response times.',
    size: 'normal',
  },
  {
    icon: Puzzle,
    title: 'Integrations',
    description: 'Connect the tools you already use, and take action from one place.',
    size: 'normal',
  },
  {
    icon: Sparkles,
    title: 'AI',
    description: 'Intelligence, context, and power at the core of every interaction.',
    size: 'large',
    featured: true,
  },
];

export default function Foundations() {
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
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        {/* Header */}
        <div className="max-w-2xl mb-16">
          <h2 className={`text-3xl sm:text-4xl lg:text-5xl font-semibold text-slate-900 mb-4 transition-all duration-700 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
            Built on <span className="text-gradient">rock-solid foundations</span>
          </h2>
          <p className={`text-lg text-slate-600 transition-all duration-700 delay-100 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
            The foundations of fast, reliable, and consistent knowledge management.
          </p>
          <a 
            href="#" 
            className={`inline-flex items-center gap-2 text-blue-600 font-medium mt-4 hover:text-blue-700 group transition-all duration-700 delay-200 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}
          >
            Discover our AI platform
            <ArrowRight className="w-4 h-4 transition-transform group-hover:translate-x-1" />
          </a>
        </div>

        {/* Bento Grid */}
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
          {foundations.map((item, index) => (
            <div
              key={item.title}
              className={`
                group relative rounded-2xl p-8 border transition-all duration-500 hover:-translate-y-1
                ${item.featured 
                  ? 'md:col-span-2 lg:col-span-1 bg-gradient-to-br from-blue-600 to-blue-800 text-white border-blue-500' 
                  : 'bg-slate-50 border-slate-200 hover:border-blue-300 hover:shadow-lg'
                }
                ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-8'}
              `}
              style={{ transitionDelay: `${(index + 2) * 100}ms` }}
            >
              {/* Icon */}
              <div className={`
                w-12 h-12 rounded-xl flex items-center justify-center mb-6 transition-transform duration-300 group-hover:scale-110
                ${item.featured ? 'bg-white/20' : 'bg-white'}
              `}>
                <item.icon className={`w-6 h-6 ${item.featured ? 'text-white' : 'text-blue-600'}`} />
              </div>

              {/* Content */}
              <h3 className={`text-xl font-semibold mb-2 ${item.featured ? 'text-white' : 'text-slate-900'}`}>
                {item.title}
              </h3>
              <p className={`leading-relaxed ${item.featured ? 'text-blue-100' : 'text-slate-600'}`}>
                {item.description}
              </p>

              {/* Featured glow effect */}
              {item.featured && (
                <div className="absolute inset-0 rounded-2xl bg-gradient-to-br from-blue-400/20 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
