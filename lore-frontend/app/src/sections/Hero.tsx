import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Star, Sparkles, MessageSquare, FileText, Users, X, ArrowRight } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

const DEMO_VIDEO_ID = '1EhgoSS3EJ0PGRSQLyYcpOGJ2SrTww70-';

export default function Hero() {
  const heroRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const [showDemo, setShowDemo] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add('visible');
          }
        });
      },
      { threshold: 0.1, rootMargin: '0px 0px -10% 0px' }
    );

    const elements = contentRef.current?.querySelectorAll('.scroll-animate');
    elements?.forEach((el) => observer.observe(el));

    return () => observer.disconnect();
  }, []);

  return (
    <section 
      ref={heroRef}
      className="relative min-h-screen pt-20 overflow-hidden bg-gradient-to-br from-white via-blue-50/30 to-white"
    >
      {/* Background gradient mesh */}
      <div className="absolute inset-0 overflow-hidden">
        <div className="absolute -top-1/2 -right-1/4 w-[800px] h-[800px] rounded-full bg-blue-100/40 blur-3xl animate-pulse-slow" />
        <div className="absolute -bottom-1/4 -left-1/4 w-[600px] h-[600px] rounded-full bg-blue-50/60 blur-3xl animate-float-slow" />
      </div>

      {/* Floating accent icons */}
      <div className="absolute inset-0 pointer-events-none overflow-hidden">
        <div className="absolute top-1/4 left-[10%] animate-float opacity-20">
          <Sparkles className="w-8 h-8 text-blue-600" />
        </div>
        <div className="absolute top-1/3 right-[15%] animate-float-slow opacity-15" style={{ animationDelay: '1s' }}>
          <MessageSquare className="w-10 h-10 text-blue-500" />
        </div>
        <div className="absolute bottom-1/3 left-[8%] animate-float opacity-10" style={{ animationDelay: '2s' }}>
          <FileText className="w-12 h-12 text-blue-400" />
        </div>
        <div className="absolute top-1/2 right-[10%] animate-float-slow opacity-15" style={{ animationDelay: '1.5s' }}>
          <Users className="w-8 h-8 text-blue-600" />
        </div>
      </div>

      <div ref={contentRef} className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 pt-16 lg:pt-24">
        <div className="grid lg:grid-cols-2 gap-12 lg:gap-16 items-center">
          {/* Content */}
          <div className="text-center lg:text-left">
            {/* Badge */}
            <div className="scroll-animate inline-flex items-center gap-2 px-4 py-2 rounded-full bg-blue-100 text-blue-800 text-sm font-medium mb-6">
              <Sparkles className="w-4 h-4" />
              <span>AI-Powered Knowledge Management</span>
            </div>

            {/* Headline */}
            <h1 className="scroll-animate text-4xl sm:text-5xl lg:text-6xl xl:text-7xl font-semibold text-slate-900 leading-tight mb-6">
              Your company's{' '}
              <span className="text-gradient">knowledge</span>
              <br />
              -captured
            </h1>

            {/* Subheadline */}
            <p className="scroll-animate text-lg sm:text-xl text-slate-600 max-w-xl mx-auto lg:mx-0 mb-8 leading-relaxed">
              AI that learns from every conversation, decision, and code change. 
              When someone leaves, their knowledge stays.
            </p>

            {/* CTAs */}
            <div className="scroll-animate flex flex-col sm:flex-row gap-4 justify-center lg:justify-start mb-10">
              <Button
                size="lg"
                onClick={() => navigate('/onboarding')}
                className="bg-blue-700 hover:bg-blue-800 text-white rounded-full px-8 py-6 text-base font-medium btn-magnetic shadow-lg shadow-blue-700/25"
              >
                Get Started
                <ArrowRight className="w-5 h-5 ml-2" />
              </Button>
              <a href="#demo">
                <Button
                  size="lg"
                  variant="outline"
                  className="rounded-full px-8 py-6 text-base font-medium border-slate-300 hover:bg-slate-50"
                >
                  Watch demo
                </Button>
              </a>
            </div>

            {/* Social Proof */}
            <div className="scroll-animate flex items-center gap-2 justify-center lg:justify-start">
              <div className="flex">
                {[1, 2, 3, 4, 5].map((i) => (
                  <Star key={i} className="w-4 h-4 fill-amber-400 text-amber-400" />
                ))}
              </div>
              <span className="text-sm text-slate-600">
                Trusted by <strong className="text-slate-900">top teams</strong>
              </span>
            </div>
          </div>

          {/* Device Mockups */}
          <div className="scroll-animate relative lg:pl-8">
            <div className="relative animate-float" style={{ animationDuration: '8s' }}>
              {/* Main device image */}
              <div className="relative rounded-2xl overflow-hidden shadow-2xl shadow-blue-900/20 border border-slate-200">
                <img 
                  src="/images/hero-devices.jpg" 
                  alt="Loremind Dashboard" 
                  className="w-full h-auto"
                />
                
                {/* Overlay gradient */}
                <div className="absolute inset-0 bg-gradient-to-t from-blue-900/10 to-transparent pointer-events-none" />
              </div>

              {/* Floating stats card */}
              <div className="absolute -bottom-6 -left-6 bg-white rounded-xl shadow-xl p-4 border border-slate-100 animate-float-slow">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-lg bg-green-100 flex items-center justify-center">
                    <div className="w-3 h-3 rounded-full bg-green-500" />
                  </div>
                  <div>
                    <div className="text-sm font-medium text-slate-900">Knowledge Preserved</div>
                    <div className="text-xs text-slate-500">98.5% capture rate</div>
                  </div>
                </div>
              </div>

              {/* Floating notification */}
              <div className="absolute -top-4 -right-4 bg-white rounded-xl shadow-xl p-3 border border-slate-100 animate-float" style={{ animationDelay: '1s' }}>
                <div className="flex items-center gap-2">
                  <div className="w-8 h-8 rounded-lg bg-blue-100 flex items-center justify-center">
                    <MessageSquare className="w-4 h-4 text-blue-600" />
                  </div>
                  <div className="text-xs">
                    <div className="font-medium text-slate-900">AI Answer</div>
                    <div className="text-slate-500">Found in 0.3s</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Bottom gradient fade */}
      <div className="absolute bottom-0 left-0 right-0 h-32 bg-gradient-to-t from-white to-transparent pointer-events-none" />

      {/* Demo Video Modal */}
      {showDemo && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4"
          onClick={() => setShowDemo(false)}
        >
          <div
            className="relative w-full max-w-4xl rounded-2xl overflow-hidden shadow-2xl bg-black"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Close button */}
            <button
              onClick={() => setShowDemo(false)}
              className="absolute top-3 right-3 z-10 w-9 h-9 rounded-full bg-white/10 hover:bg-white/20 flex items-center justify-center text-white transition-colors"
            >
              <X className="w-5 h-5" />
            </button>

            {/* Video iframe */}
            <div className="relative w-full" style={{ paddingTop: '56.25%' }}>
              <iframe
                src={`https://drive.google.com/file/d/${DEMO_VIDEO_ID}/preview`}
                className="absolute inset-0 w-full h-full"
                allow="autoplay"
                allowFullScreen
              />
            </div>
          </div>
        </div>
      )}

    </section>
  );
}
