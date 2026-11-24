import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Menu, X } from 'lucide-react';

const navLinks = [
  { name: 'About', href: '#story' },
  { name: 'Product', href: '#values' },
  { name: 'Pricing', href: '#pricing' },
  { name: 'FAQ', href: '#faq' },
  { name: 'Contact', href: '#footer' },
];

interface NavbarProps {
  onContactClick?: () => void;
}

export default function Navbar({ onContactClick }: NavbarProps) {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);


  return (
    <nav className="fixed top-0 left-0 right-0 z-50 flex justify-center pointer-events-none"
      style={{ paddingTop: '12px' }}
    >
      {/* Floating Glass Pill Capsule */}
      <div
        className="relative pointer-events-auto flex items-center transition-all duration-300"
        style={{
          width: 'calc(100% - 24px)',
          maxWidth: '1200px',
          height: '64px',
          borderRadius: '9999px',
          background: 'rgba(255, 255, 255, 0.72)',
          backdropFilter: 'blur(20px)',
          WebkitBackdropFilter: 'blur(20px)',
          border: '1.5px solid rgba(255, 255, 255, 0.8)',
          boxShadow: '0 8px 32px rgba(0, 0, 0, 0.08), inset -1px -1px 2px rgba(0, 0, 0, 0.05), inset 1px 1px 3px rgba(255, 255, 255, 0.9)',
        }}
      >
        {/* Content Container */}
        <div className="relative z-10 flex items-center justify-between w-full h-full px-4 sm:px-6">
          {/* Left - Logo */}
          <a href="/" className="flex items-center flex-shrink-0 transition-transform duration-300 hover:scale-105">
            <img src="/images/logo.png" alt="Loremind" className="h-7 sm:h-8 w-auto" />
          </a>

          {/* Center - Navigation Links (Desktop) */}
          <div className="hidden lg:flex items-center justify-center flex-1 gap-12">
            {navLinks.map((link) => (
              <a
                key={link.name}
                href={link.href}
                className="text-sm font-500 text-slate-700 transition-all duration-300 relative group whitespace-nowrap"
                style={{
                  letterSpacing: '0.3px'
                }}
              >
                {link.name}
                <span className="absolute bottom-0 left-0 w-0 h-0.5 bg-gradient-to-r from-blue-600 to-blue-400 transition-all duration-300 group-hover:w-full rounded-full" />
              </a>
            ))}
          </div>

          {/* Right - Nav Actions */}
          <div className="hidden lg:flex items-center gap-3 flex-shrink-0"
            style={{ paddingRight: '26px' }}
          >
            <button
              onClick={onContactClick}
              className="text-sm font-500 text-slate-700 hover:text-slate-900 transition-all duration-300 px-4 py-2 rounded-full border border-slate-200/80 hover:border-slate-300 hover:bg-white/60"
            >
              Schedule Demo
            </button>
            <Link to="/login">
              <Button
                className="text-sm font-600 text-white hover:opacity-90 transition-all duration-300 rounded-full px-5 py-2"
                style={{
                  background: 'linear-gradient(135deg, #0a3fb5 0%, #3259ce 100%)',
                  boxShadow: '0 6px 18px rgba(10,63,181,0.25)',
                }}
              >
                Login
              </Button>
            </Link>
          </div>

          {/* Mobile Menu Button */}
          <button
            className="lg:hidden p-2 mr-4 transition-all duration-300 rounded-full"
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            style={{
              background: 'rgba(255, 255, 255, 0.15)',
              backdropFilter: 'blur(12px)',
              WebkitBackdropFilter: 'blur(12px)'
            }}
          >
            {mobileMenuOpen ? <X className="w-5 h-5 text-slate-700" /> : <Menu className="w-5 h-5 text-slate-700" />}
          </button>
        </div>
      </div>

      {/* Mobile Menu */}
      {mobileMenuOpen && (
        <div
          className="absolute top-full left-1/2 transform -translate-x-1/2 mt-4 pointer-events-auto animate-in fade-in slide-in-from-top-2 duration-300"
          style={{
            width: '85%',
            maxWidth: '1200px',
          }}
        >
          <div
            className="rounded-3xl p-6 space-y-2"
            style={{
              background: 'rgba(255, 255, 255, 0.42)',
              backdropFilter: 'blur(60px)',
              WebkitBackdropFilter: 'blur(60px)',
              border: '1.5px solid rgba(255, 255, 255, 0.8)',
              boxShadow: 'inset -1px -1px 2px rgba(0, 0, 0, 0.05), inset 1px 1px 3px rgba(255, 255, 255, 0.9), 0 8px 32px rgba(0, 0, 0, 0.08)',
            }}
          >
            {navLinks.map((link) => (
              <a
                key={link.name}
                href={link.href}
                className="block px-4 py-3 text-sm font-500 text-slate-700 hover:text-slate-950 rounded-2xl transition-all duration-300"
                onClick={() => setMobileMenuOpen(false)}
                style={{
                  background: 'rgba(255, 255, 255, 0.1)',
                }}
              >
                {link.name}
              </a>
            ))}
            <div className="pt-3 border-t border-white/30 mt-3 flex flex-col gap-2">
              <button
                onClick={() => { setMobileMenuOpen(false); onContactClick?.(); }}
                className="w-full py-2.5 text-sm font-500 text-slate-700 rounded-full border border-slate-200/80 hover:bg-white/60 transition-all duration-300"
              >
                Schedule Demo
              </button>
              <Link to="/login" onClick={() => setMobileMenuOpen(false)}>
                <Button
                  className="w-full text-sm font-600 text-white hover:opacity-90 transition-all duration-300 rounded-full"
                  style={{
                    background: 'linear-gradient(135deg, #0a3fb5 0%, #3259ce 100%)',
                  }}
                >
                  Login
                </Button>
              </Link>
            </div>
          </div>
        </div>
      )}
    </nav>
  );
}
