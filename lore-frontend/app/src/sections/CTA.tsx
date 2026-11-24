import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Check, Sparkles, Mail } from 'lucide-react';
import ContactModal from '@/components/ContactModal';

const benefits = [
  'Free 14-day trial',
  'No credit card required',
  'Setup in under 10 minutes',
];

export default function CTA() {
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
      className="py-24 lg:py-32 relative overflow-hidden"
    >
      {/* Background gradient */}
      <div className="absolute inset-0 bg-gradient-to-br from-blue-800 via-blue-700 to-blue-900">
        {/* Animated gradient overlay */}
        <div className="absolute inset-0 bg-gradient-to-r from-blue-600/20 via-transparent to-blue-800/20 animate-gradient" />
        
        {/* Floating shapes */}
        <div className="absolute top-10 left-10 w-64 h-64 rounded-full bg-blue-500/20 blur-3xl animate-float" />
        <div className="absolute bottom-10 right-10 w-96 h-96 rounded-full bg-blue-400/10 blur-3xl animate-float-slow" />
      </div>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 relative">
        <div className="grid lg:grid-cols-2 gap-12 lg:gap-16 items-center">
          {/* Content */}
          <div>
            <div className={`inline-flex items-center gap-2 px-4 py-2 rounded-full bg-white/10 text-blue-100 text-sm font-medium mb-6 backdrop-blur-sm transition-all duration-700 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
              <Sparkles className="w-4 h-4" />
              <span>Start preserving knowledge today</span>
            </div>

            <h2 className={`text-3xl sm:text-4xl lg:text-5xl font-semibold text-white mb-6 leading-tight transition-all duration-700 delay-100 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
              Ready to capture your company's knowledge?
            </h2>

            <p className={`text-lg text-blue-100 mb-8 transition-all duration-700 delay-200 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
              Join 500+ teams who've stopped losing institutional knowledge.
            </p>

            {/* CTAs */}
            <div className={`flex flex-col sm:flex-row gap-4 mb-8 transition-all duration-700 delay-300 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
              <Button
                size="lg"
                onClick={() => setShowContact(true)}
                className="bg-white text-blue-700 hover:bg-blue-50 rounded-full px-8 py-6 text-base font-medium btn-magnetic shadow-lg"
              >
                Contact us
                <Mail className="w-5 h-5 ml-2" />
              </Button>
              {/* <Button 
                size="lg" 
                variant="outline" 
                className="rounded-full px-8 py-6 text-base font-medium border-white/30 text-white hover:bg-white/10 backdrop-blur-sm"
              >
                Watch demo
              </Button> */}
            </div>

            {/* Benefits */}
            <div className={`space-y-3 transition-all duration-700 delay-400 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
              {benefits.map((benefit) => (
                <div key={benefit} className="flex items-center gap-3">
                  <div className="w-5 h-5 rounded-full bg-green-400/20 flex items-center justify-center">
                    <Check className="w-3 h-3 text-green-300" />
                  </div>
                  <span className="text-blue-100">{benefit}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Image */}
          <div className={`relative transition-all duration-700 delay-300 ${isVisible ? 'opacity-100 translate-x-0' : 'opacity-0 translate-x-12'}`}>
            <div className="relative animate-float" style={{ animationDuration: '6s' }}>
              <img 
                src="/images/cta-laptop.jpg" 
                alt="Loremind Dashboard"
                className="rounded-2xl shadow-2xl"
              />
              
              {/* Glow effect */}
              <div className="absolute -inset-4 bg-blue-400/20 rounded-3xl blur-2xl -z-10" />
            </div>

            {/* Floating badge */}
            <div className="absolute -bottom-4 -left-4 bg-white rounded-xl shadow-xl p-4 animate-float-slow">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-lg bg-green-100 flex items-center justify-center">
                  <Check className="w-5 h-5 text-green-600" />
                </div>
                <div>
                  <div className="text-sm font-medium text-slate-900">Setup Complete</div>
                  <div className="text-xs text-slate-500">Knowledge flowing</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
      {/* Contact Modal */}
      <ContactModal open={showContact} onClose={() => setShowContact(false)} />
    </section>
  );
}
