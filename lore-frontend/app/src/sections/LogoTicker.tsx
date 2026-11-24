import { useEffect, useRef, useState } from 'react';
import { Mail, Github, Video, Ticket, MailOpen, MessageSquare, GitMerge } from 'lucide-react';

const channels = [
  { name: 'Gmail', icon: Mail },
  { name: 'GitHub', icon: Github },
  { name: 'Google Meet', icon: Video },
  { name: 'Jira', icon: Ticket },
  { name: 'Outlook', icon: MailOpen },
  { name: 'Slack', icon: MessageSquare },
  { name: 'GitLab', icon: GitMerge },
];

export default function LogoTicker() {
  const [isVisible, setIsVisible] = useState(false);
  const sectionRef = useRef<HTMLDivElement>(null);

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
      className="py-16 bg-white border-y border-slate-200 overflow-hidden"
    >
      <div className={`max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 mb-8 transition-all duration-700 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
        <p className="text-center text-sm font-medium text-slate-500 uppercase tracking-wider">
          Integrate with any communication channel
        </p>
      </div>

      <div className={`relative transition-all duration-700 delay-200 ${isVisible ? 'opacity-100' : 'opacity-0'}`}>
        <div className="absolute left-0 top-0 bottom-0 w-32 bg-gradient-to-r from-white to-transparent z-10" />
        <div className="absolute right-0 top-0 bottom-0 w-32 bg-gradient-to-l from-white to-transparent z-10" />

        <div className="flex animate-ticker">
          {[...channels, ...channels, ...channels].map((channel, i) => (
            <div
              key={i}
              className="flex-shrink-0 px-8 py-4 group"
            >
              <div className="flex items-center gap-3 text-slate-300 group-hover:text-blue-600 transition-colors duration-300 cursor-default">
                <channel.icon className="w-7 h-7" />
                <span className="text-xl font-semibold whitespace-nowrap">{channel.name}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
