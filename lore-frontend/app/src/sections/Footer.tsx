import { Linkedin, Mail } from 'lucide-react';

function XIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className}>
      <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
    </svg>
  );
}

const socialLinks = [
  { icon: XIcon, href: 'https://x.com/Loremind_forge', label: 'X' },
  { icon: Linkedin, href: 'https://www.linkedin.com/in/loremind-forge-8540763b7/', label: 'LinkedIn' },
];

export default function Footer() {
  return (
    <footer className="bg-white border-t border-slate-200">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-16">
        <div className="flex flex-col items-center text-center">
          {/* Logo */}
          <a href="#" className="flex items-center gap-2 mb-6">
            <img src="/images/logo.png" alt="Loremind" className="h-10 w-auto" />
          </a>

          <p className="text-slate-600 mb-6 max-w-sm">
            AI-powered knowledge management that captures every decision,
            conversation, and insight.
          </p>

          {/* Email */}
          <a
            href="mailto:founders@loremind.in"
            className="inline-flex items-center gap-2 text-slate-600 hover:text-blue-600 transition-colors mb-6"
          >
            <Mail className="w-4 h-4" />
            founders@loremind.in
          </a>

          {/* Social Links */}
          <div className="flex gap-3">
            {socialLinks.map((social) => (
              <a
                key={social.label}
                href={social.href}
                aria-label={social.label}
                className="w-10 h-10 rounded-lg bg-slate-100 flex items-center justify-center text-slate-600 hover:bg-blue-100 hover:text-blue-600 transition-colors"
              >
                <social.icon className="w-5 h-5" />
              </a>
            ))}
          </div>
        </div>

        {/* Bottom Bar */}
        <div className="mt-12 pt-8 border-t border-slate-200 flex flex-col sm:flex-row justify-between items-center gap-4">
          <p className="text-sm text-slate-500">
            &copy; 2026 Loremind Forge Private Limited. All rights reserved.
          </p>
          <div className="flex gap-6">
            <a href="#" className="text-sm text-slate-500 hover:text-blue-600 transition-colors">
              Privacy Policy
            </a>
            <a href="#" className="text-sm text-slate-500 hover:text-blue-600 transition-colors">
              Terms of Service
            </a>
          </div>
        </div>
      </div>
    </footer>
  );
}
