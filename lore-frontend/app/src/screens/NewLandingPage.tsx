import React, { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { motion, useScroll, useTransform, useReducedMotion, AnimatePresence } from 'framer-motion';
import {
  Search, Brain, Zap,
  Sparkles, X, Check, Server,
  Cloud, Mail, Twitter, Linkedin,
  Plug2, GitMerge, Lock, MessageCircle, TrendingUp,
  Plus
} from 'lucide-react';
import ContactModal from '../components/ContactModal';
import Navbar from '../sections/Navbar';
import { StorylineAnimation } from '../sections/StorylineAnimation';

const FONT_DISPLAY = "var(--font-display)";
const FONT_BODY = "var(--font-body)";

// ============================================
// HERO BACKGROUND CANVAS
// ============================================
const KnowledgeGraphCanvas: React.FC = () => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animationRef = useRef<number>(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let nodes: Array<{ x: number; y: number; vx: number; vy: number; r: number; o: number }> = [];

    const resize = () => {
      canvas.width = canvas.offsetWidth;
      canvas.height = canvas.offsetHeight;
      nodes = Array.from({ length: 60 }, () => ({
        x: Math.random() * canvas.width,
        y: Math.random() * canvas.height,
        vx: (Math.random() - 0.5) * 0.3,
        vy: (Math.random() - 0.5) * 0.3,
        r: 2 + Math.random() * 2,
        o: 0.15 + Math.random() * 0.25,
      }));
    };

    const animate = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      nodes.forEach((n, i) => {
        n.x += n.vx; n.y += n.vy;
        if (n.x < 0 || n.x > canvas.width) n.vx *= -1;
        if (n.y < 0 || n.y > canvas.height) n.vy *= -1;
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(10, 63, 181, ${n.o})`;
        ctx.fill();
        for (let j = i + 1; j < nodes.length; j++) {
          const o = nodes[j];
          const d = Math.hypot(n.x - o.x, n.y - o.y);
          if (d < 120) {
            ctx.beginPath();
            ctx.moveTo(n.x, n.y);
            ctx.lineTo(o.x, o.y);
            ctx.strokeStyle = `rgba(10, 63, 181, ${(1 - d / 120) * 0.12})`;
            ctx.lineWidth = 1;
            ctx.stroke();
          }
        }
      });
      animationRef.current = requestAnimationFrame(animate);
    };

    resize();
    window.addEventListener('resize', resize);
    animate();
    return () => { window.removeEventListener('resize', resize); cancelAnimationFrame(animationRef.current); };
  }, []);

  return <canvas ref={canvasRef} className="absolute inset-0 w-full h-full pointer-events-none" />;
};

// ============================================
// NAVIGATION
// ============================================
const Navigation: React.FC<{ onContactClick: () => void }> = ({ onContactClick }) => {
  return <Navbar onContactClick={onContactClick} />;
};

// ============================================
// HERO DEMO PANEL
// ============================================
const DEMO_QUERIES = [
  {
    query: "What did we decide about the API rate limits?",
    insight: <>Rate limits set at <strong>1000 req/min</strong>, finalized in architecture review. Decision recorded across Slack, Jira, and GitHub.</>,
    citations: [
      { source: 'SLACK', text: "Rate limits at 1000 req/min per architecture review", author: '@sarah', time: '3mo ago' },
      { source: 'JIRA', text: "Ticket #4421 - API throttling approved by CTO", author: '@arjun', time: '2mo ago' },
      { source: 'GITHUB', text: "PR #892 - enforce-rate-limits merged", author: '@dev-team', time: '6w ago' },
    ],
  },
  {
    query: "Why did we switch the packaging vendor last quarter?",
    insight: <>Vendor switched from PackRight to BoxCo due to <strong>18% cost reduction</strong> and faster delivery SLA. Approved by ops lead after 3-week trial.</>,
    citations: [
      { source: 'GMAIL', text: "BoxCo proposal with revised pricing - 18% below PackRight", author: '@procurement', time: '4mo ago' },
      { source: 'SLACK', text: "Trial results: BoxCo hit 98% on-time vs PackRight 91%", author: '@ops-lead', time: '3mo ago' },
      { source: 'JIRA', text: "OPS-287 - Vendor migration to BoxCo completed", author: '@logistics', time: '2mo ago' },
    ],
  },
  {
    query: "What was the outcome of the Acme Corp renewal call?",
    insight: <>Acme Corp renewed for <strong>2 years at $240k ARR</strong>, up 15% from prior contract. They requested dedicated onboarding for 3 new teams.</>,
    citations: [
      { source: 'GMAIL', text: "Acme renewal signed - 2yr, $240k ARR, 15% uplift", author: '@deals', time: '2w ago' },
      { source: 'SLACK', text: "Acme wants onboarding for marketing, sales ops, and finance", author: '@cs-lead', time: '10d ago' },
      { source: 'JIRA', text: "CS-1102 - Acme onboarding kickoff scheduled for May 5", author: '@onboarding', time: '1w ago' },
    ],
  },
];

const SOURCE_LOGOS: Record<string, string> = {
  SLACK: '/images/slack.png',
  JIRA: '/images/jira.jpg',
  GITHUB: '/images/github.png',
  GMAIL: '/images/gmail.png',
};

const SOURCE_COLORS: Record<string, string> = { SLACK: '#9f7aea', JIRA: '#0a3fb5', GITHUB: '#1a1c1d', GMAIL: '#ea4335' };

const SOURCE_NAMES: Record<string, string> = { SLACK: 'Slack', JIRA: 'Jira', GITHUB: 'GitHub', GMAIL: 'Gmail' };

const HeroDemoPanel: React.FC = () => {
  const [queryIndex, setQueryIndex] = useState(0);
  const [step, setStep] = useState(0);
  const [text, setText] = useState('');
  const demo = DEMO_QUERIES[queryIndex];

  // Typing effect
  useEffect(() => {
    if (step === 0) {
      let i = 0;
      setText('');
      const t = setInterval(() => {
        if (i <= demo.query.length) { setText(demo.query.slice(0, i)); i++; }
        else { clearInterval(t); setTimeout(() => setStep(1), 200); }
      }, 22);
      return () => clearInterval(t);
    }
  }, [step, queryIndex]);

  // Step progression
  useEffect(() => {
    if (step === 0) return;
    const timers: ReturnType<typeof setTimeout>[] = [];
    if (step === 1) timers.push(setTimeout(() => setStep(2), 600));
    else if (step === 2) timers.push(setTimeout(() => setStep(3), 800));
    else if (step === 3) timers.push(setTimeout(() => setStep(4), 1400));
    else if (step === 4) timers.push(setTimeout(() => {
      setStep(0);
      setText('');
      setQueryIndex(prev => (prev + 1) % DEMO_QUERIES.length);
    }, 3500));
    return () => timers.forEach(clearTimeout);
  }, [step]);

  return (
    <div className="relative w-full max-w-[440px] sm:max-w-[520px] h-[380px] sm:h-[440px] rounded-2xl p-3 sm:p-5 bg-white border border-[rgba(196,197,214,0.3)] flex flex-col"
      style={{ boxShadow: '0px 20px 40px rgba(10, 63, 181, 0.06)' }}>
      {/* Title bar */}
      <div className="flex items-center gap-2 mb-2 sm:mb-3">
        <div className="flex gap-1.5">
          <div className="w-2 h-2 rounded-full bg-red-400/60" />
          <div className="w-2 h-2 rounded-full bg-yellow-400/60" />
          <div className="w-2 h-2 rounded-full bg-green-400/60" />
        </div>
        <span className="ml-3 text-[9px] sm:text-[10px] text-[#6b7280] uppercase tracking-[0.12em]" style={{ fontFamily: FONT_BODY }}>LoreMind Search</span>
      </div>

      {/* Search bar */}
      <div className="bg-[#f3f3f4] rounded-lg px-3 sm:px-4 py-2 sm:py-2.5 flex items-center gap-2 sm:gap-3 mb-2 sm:mb-3 shrink-0">
        <Search size={14} className="text-[#6b7280] flex-shrink-0" />
        <span className="text-[12px] sm:text-[13px] text-[#1a1c1d] truncate" style={{ fontFamily: FONT_BODY }}>
          {text}<span className="animate-pulse text-[#0a3fb5]">|</span>
        </span>
      </div>

      {/* Searching across pills with logos - only sources cited in this query */}
      {step >= 1 && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mb-2 sm:mb-3 shrink-0">
          <span className="text-[8px] sm:text-[9px] text-[#6b7280] uppercase tracking-wider block mb-1 sm:mb-1.5" style={{ fontFamily: FONT_BODY }}>Searching across...</span>
          <div className="flex gap-1 sm:gap-1.5 flex-wrap">
            {Array.from(new Set(demo.citations.map(c => c.source))).map((src, i) => (
              <motion.div key={src}
                initial={{ opacity: 0.5, borderColor: 'rgba(196,197,214,0.3)' }}
                animate={step >= 2 ? { opacity: 1, borderColor: '#0a3fb5', boxShadow: '0 0 12px rgba(10,63,181,0.1)' } : {}}
                transition={{ delay: i * 0.15 }}
                className="flex items-center gap-1 sm:gap-1.5 px-2 sm:px-2.5 py-0.5 sm:py-1 rounded-full border text-[8px] sm:text-[9px] bg-white" style={{ fontFamily: FONT_BODY }}>
                <img src={SOURCE_LOGOS[src]} alt={SOURCE_NAMES[src]} className="w-3 h-3 sm:w-4 sm:h-4 object-contain rounded-sm" />
                <span className="hidden sm:inline">{SOURCE_NAMES[src]}</span>
              </motion.div>
            ))}
          </div>
        </motion.div>
      )}

      {/* Knowledge Insight - shown first */}
      {step >= 3 && (
        <motion.div key={`insight-${queryIndex}`} initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }}
          className="rounded-lg p-2 sm:p-3 border border-[rgba(10,63,181,0.2)] mb-2 sm:mb-3 shrink-0" style={{ background: 'rgba(10,63,181,0.04)' }}>
          <div className="flex items-center gap-1 sm:gap-2 mb-1 sm:mb-1.5">
            <Sparkles size={12} className="text-[#0a3fb5] flex-shrink-0" />
            <span className="text-[9px] sm:text-[10px] font-semibold text-[#0a3fb5] uppercase tracking-[0.08em]" style={{ fontFamily: FONT_BODY }}>Knowledge Insight</span>
          </div>
          <p className="text-[11px] sm:text-[12px] text-[#1a1c1d] leading-[1.4] sm:leading-[1.5]" style={{ fontFamily: FONT_BODY }}>
            {demo.insight}
          </p>
        </motion.div>
      )}

      {/* Citations */}
      {step >= 4 && (
        <motion.div key={`citations-${queryIndex}`} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="flex-1 min-h-0">
          <span className="text-[8px] sm:text-[9px] text-[#6b7280] uppercase tracking-[0.1em] block mb-1 sm:mb-1.5 font-semibold" style={{ fontFamily: FONT_BODY }}>Sources &middot; {demo.citations.length} found</span>
          <div className="space-y-1 sm:space-y-1.5 overflow-y-auto max-h-[200px] sm:max-h-none pr-1">
            {demo.citations.map((c, i) => (
              <motion.div key={i} initial={{ opacity: 0, x: -12 }} animate={{ opacity: 1, x: 0 }}
                transition={{ delay: i * 0.15 }}
                className="flex items-start gap-1.5 sm:gap-2.5 rounded-md px-2 sm:px-2.5 py-1.5 sm:py-2 bg-[#f9f9fa]">
                <img src={SOURCE_LOGOS[c.source] || ''} alt={c.source} className="w-3 h-3 sm:w-4 sm:h-4 object-contain rounded-sm shrink-0 mt-0.5" />
                <div className="min-w-0">
                  <div className="flex items-center gap-1 sm:gap-2 mb-0.5 flex-wrap">
                    <span className="text-[7px] sm:text-[9px] font-bold uppercase tracking-wider" style={{ fontFamily: FONT_BODY, color: SOURCE_COLORS[c.source] || '#6b7280' }}>{c.source}</span>
                    <span className="text-[7px] sm:text-[9px] text-[#6b7280] hidden sm:inline" style={{ fontFamily: FONT_BODY }}>{c.author} &middot; {c.time}</span>
                  </div>
                  <p className="text-[10px] sm:text-[11px] text-[#1a1c1d] leading-snug" style={{ fontFamily: FONT_BODY }}>{c.text}</p>
                </div>
              </motion.div>
            ))}
          </div>
        </motion.div>
      )}
    </div>
  );
};

// ============================================
// SECTION 1 - HERO
// ============================================
const HeroSection: React.FC<{ onContactClick: () => void }> = ({ onContactClick }) => {
  const reduce = useReducedMotion();
  const headline = "Your company's knowledge is buried in conversations.";
  const words = headline.split(' ');

  return (
    <section className="relative min-h-screen overflow-hidden pt-16 bg-white">
      <KnowledgeGraphCanvas />
      <div className="relative z-10 max-w-[1280px] mx-auto px-4 sm:px-6 lg:px-8 py-12 sm:py-20 lg:py-28">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 sm:gap-12 lg:items-start">
          <div className="text-left lg:pt-8">
            <motion.div initial={reduce ? {} : { opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}
              className="text-[9px] sm:text-[10px] text-[#0a3fb5] uppercase tracking-[0.16em] mb-4 sm:mb-6 font-semibold" style={{ fontFamily: FONT_BODY }}>
              STOP LOSING WHAT YOUR TEAM ALREADY KNOWS
            </motion.div>

            <h1 className="text-[clamp(28px,6vw,52px)] font-[700] text-[#1a1c1d] leading-[1.08] tracking-[-0.025em] max-w-[560px]"
              style={{ fontFamily: FONT_DISPLAY }}>
              {words.map((word, i) => (
                <motion.span key={i}
                  initial={reduce ? {} : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.4, delay: i * 0.06, ease: 'easeOut' }}
                  className={`inline-block mr-[0.25em] ${word === 'buried' ? 'text-[#0a3fb5]' : ''}`}>
                  {word}
                </motion.span>
              ))}
            </h1>

            <motion.p initial={reduce ? {} : { opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.4, delay: 0.4 }}
              className="mt-4 sm:mt-6 text-[14px] sm:text-[16px] text-[#6b7280] leading-[1.7] max-w-[480px]" style={{ fontFamily: FONT_BODY }}>
              LoreMind bridges the gap between raw internal data and actionable intelligence. Capture, organize, and query your entire institutional memory.
            </motion.p>

            <motion.div initial={reduce ? {} : { opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.4, delay: 0.5 }} className="flex flex-wrap items-center gap-3 sm:gap-4 mt-6 sm:mt-8">
              <button onClick={onContactClick} className="px-4 sm:px-6 py-2.5 sm:py-3 text-[12px] sm:text-[14px] font-semibold text-white rounded-[10px] hover:shadow-lg transition-all whitespace-nowrap"
                style={{ fontFamily: FONT_BODY, background: 'linear-gradient(135deg, #0a3fb5 0%, #3259ce 100%)' }}>
                Schedule a Demo
              </button>
              <a href="#values" className="px-4 sm:px-6 py-2.5 sm:py-3 text-[12px] sm:text-[14px] font-medium text-[#1a1c1d] border border-[rgba(196,197,214,0.4)] rounded-[10px] hover:border-[#0a3fb5] transition-colors whitespace-nowrap" style={{ fontFamily: FONT_BODY }}>
                How we work
              </a>
              <Link to="/login" className="px-4 sm:px-6 py-2.5 sm:py-3 text-[12px] sm:text-[14px] font-medium text-[#0a3fb5] border border-[rgba(10,63,181,0.35)] rounded-[10px] hover:bg-[rgba(10,63,181,0.04)] transition-colors whitespace-nowrap" style={{ fontFamily: FONT_BODY }}>
                Sign in
              </Link>
            </motion.div>

            <motion.div initial={reduce ? {} : { opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.4, delay: 0.6 }}
              className="mt-4 sm:mt-5 text-[9px] sm:text-[11px] text-[#6b7280] uppercase tracking-wider" style={{ fontFamily: FONT_BODY }}>
              On-premise deployment <span className="mx-1 sm:mx-2 text-[#0a3fb5] font-bold">·</span> First month free <span className="mx-1 sm:mx-2 text-[#0a3fb5] font-bold">·</span> Role based Access
            </motion.div>
          </div>

          <motion.div initial={reduce ? {} : { opacity: 0, x: 40 }} animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.6, delay: 0.3 }} className="flex justify-center lg:justify-end lg:pt-8 mt-8 lg:mt-0">
            <HeroDemoPanel />
          </motion.div>
        </div>
      </div>
    </section>
  );
};

// ============================================
// SECTION 2 - VALUES
// ============================================
const ValueSection: React.FC = () => {
  const reduce = useReducedMotion();

  return (
    <section id="values" className="relative py-24 lg:py-40 overflow-hidden bg-white">
      <div className="max-w-[1280px] mx-auto px-6 lg:px-8">
        <motion.div initial={reduce ? {} : { opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }}
          transition={{ duration: 0.5 }} className="text-center mb-20">
          <span className="text-[10px] text-[#0a3fb5] uppercase tracking-[0.16em] block mb-4 font-semibold" style={{ fontFamily: FONT_BODY }}>CAPABILITIES</span>
          <h2 className="text-[clamp(32px,4vw,52px)] font-[700] text-[#1a1c1d] leading-[1.1] tracking-[-0.02em] uppercase" style={{ fontFamily: FONT_DISPLAY }}>
            What LoreMind Delivers
          </h2>
          <p className="text-[15px] text-[#6b7280] mt-4 max-w-[520px] mx-auto" style={{ fontFamily: FONT_BODY }}>
            With LoreMind&apos;s knowledge infrastructure, every team gets the tools they need.
          </p>
        </motion.div>

        {/* Bento Grid */}
        <div className="max-w-[1100px] mx-auto flex flex-col gap-5">
          {/* Row 1 */}
          <div className="grid grid-cols-1 md:grid-cols-12 gap-5">
            {/* Knowledge Graph - Large */}
            <motion.div
              initial={reduce ? {} : { opacity: 0, y: 40 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-80px" }}
              transition={{ duration: 0.6, ease: "easeOut" }}
              className="md:col-span-7 rounded-3xl bg-[#f7f8fb] overflow-hidden group cursor-default border border-black/[0.04]"
            >
              <div className="p-8 lg:p-10 pb-5 lg:pb-6">
                <h3 className="text-[22px] font-[700] text-[#1a1c1d] mb-2 tracking-[-0.01em]" style={{ fontFamily: FONT_DISPLAY }}>
                  Knowledge Graph
                </h3>
                <p className="text-[14px] text-[#6b7280] leading-[1.7] max-w-[380px]" style={{ fontFamily: FONT_BODY }}>
                  Visualize how people, projects, and expertise connect across your organization with an interactive knowledge map.
                </p>
              </div>
              <div className="px-5 lg:px-8">
                <img
                  src="/images/bento/graph.png"
                  alt="Knowledge graph showing connected people, projects, and topics"
                  className="w-full rounded-t-2xl shadow-[0_2px_20px_rgba(0,0,0,0.08)] transition-transform duration-500 group-hover:-translate-y-1"
                  loading="lazy"
                />
              </div>
            </motion.div>

            {/* Smart Retrieval - Small */}
            <motion.div
              initial={reduce ? {} : { opacity: 0, y: 40 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-80px" }}
              transition={{ duration: 0.6, delay: 0.12, ease: "easeOut" }}
              className="md:col-span-5 rounded-3xl bg-[#f7f8fb] overflow-hidden group cursor-default border border-black/[0.04]"
            >
              <div className="p-8 lg:p-10 pb-5 lg:pb-6">
                <h3 className="text-[22px] font-[700] text-[#1a1c1d] mb-2 tracking-[-0.01em]" style={{ fontFamily: FONT_DISPLAY }}>
                  Smart Retrieval
                </h3>
                <p className="text-[14px] text-[#6b7280] leading-[1.7]" style={{ fontFamily: FONT_BODY }}>
                  Ask questions in natural language and get instant, cited answers from your team&apos;s collective knowledge.
                </p>
              </div>
              <div className="px-5 lg:px-8">
                <img
                  src="/images/bento/chatui.png"
                  alt="Chat interface for querying organizational knowledge"
                  className="w-full rounded-t-2xl shadow-[0_2px_20px_rgba(0,0,0,0.08)] transition-transform duration-500 group-hover:-translate-y-1"
                  loading="lazy"
                />
              </div>
            </motion.div>
          </div>

          {/* Row 2 */}
          <div className="grid grid-cols-1 md:grid-cols-12 gap-5">
            {/* Team Directory - Small */}
            <motion.div
              initial={reduce ? {} : { opacity: 0, y: 40 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-80px" }}
              transition={{ duration: 0.6, ease: "easeOut" }}
              className="md:col-span-5 rounded-3xl bg-[#f7f8fb] overflow-hidden group cursor-default border border-black/[0.04]"
            >
              <div className="p-8 lg:p-10 pb-5 lg:pb-6">
                <h3 className="text-[22px] font-[700] text-[#1a1c1d] mb-2 tracking-[-0.01em]" style={{ fontFamily: FONT_DISPLAY }}>
                  Team Directory
                </h3>
                <p className="text-[14px] text-[#6b7280] leading-[1.7]" style={{ fontFamily: FONT_BODY }}>
                  Browse your organization&apos;s people, filter by department or role, and see who knows what at a glance.
                </p>
              </div>
              <div className="px-5 lg:px-8">
                <img
                  src="/images/bento/access.png"
                  alt="Team directory with department filtering and access levels"
                  className="w-full rounded-t-2xl shadow-[0_2px_20px_rgba(0,0,0,0.08)] transition-transform duration-500 group-hover:-translate-y-1"
                  loading="lazy"
                />
              </div>
            </motion.div>

            {/* Expertise Mapping - Large */}
            <motion.div
              initial={reduce ? {} : { opacity: 0, y: 40 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-80px" }}
              transition={{ duration: 0.6, delay: 0.12, ease: "easeOut" }}
              className="md:col-span-7 rounded-3xl bg-[#f7f8fb] overflow-hidden group cursor-default border border-black/[0.04]"
            >
              <div className="p-8 lg:p-10 pb-5 lg:pb-6">
                <h3 className="text-[22px] font-[700] text-[#1a1c1d] mb-2 tracking-[-0.01em]" style={{ fontFamily: FONT_DISPLAY }}>
                  Expertise Mapping
                </h3>
                <p className="text-[14px] text-[#6b7280] leading-[1.7] max-w-[380px]" style={{ fontFamily: FONT_BODY }}>
                  Map skill coverage across teams to identify knowledge gaps and find the right expert for any topic.
                </p>
              </div>
              <div className="px-5 lg:px-8">
                <img
                  src="/images/bento/expertise.png"
                  alt="Skill coverage matrix showing team expertise across topics"
                  className="w-full rounded-t-2xl shadow-[0_2px_20px_rgba(0,0,0,0.08)] transition-transform duration-500 group-hover:-translate-y-1"
                  loading="lazy"
                />
              </div>
            </motion.div>
          </div>
        </div>
      </div>
    </section>
  );
};

// ============================================
// SECTION 3 - STORY
// ============================================

const AnimatedBackgroundCircles: React.FC = () => {
  const circles = [
    { left: '10%', top: '18%', size: 280, gradient: 'rgba(10,63,181,0.55)', delay: 0 },
    { left: '78%', top: '14%', size: 250, gradient: 'rgba(58,123,245,0.52)', delay: 0.6 },
  ];

  return (
    <div className="absolute inset-0 pointer-events-none overflow-hidden">
      {circles.map((circle, index) => (
        <motion.div
          key={index}
          className="absolute rounded-full"
          style={{
            left: circle.left,
            top: circle.top,
            width: circle.size,
            height: circle.size,
            background: `radial-gradient(circle, ${circle.gradient} 0%, rgba(255,255,255,0) 55%)`,
            filter: 'blur(22px)',
            transform: 'translate(-50%, -50%)',
            opacity: 0.25,
          }}
          animate={{
            x: [0, 300, -350, 250, 0],
            y: [0, -250, 280, -150, 0],
            opacity: [0.25, 0.70, 0.25],
          }}
          transition={{
            duration: 32,
            repeat: Infinity,
            ease: 'easeInOut',
            delay: circle.delay,
          }}
        />
      ))}
    </div>
  );
};

const StorySection: React.FC = () => {
  const reduce = useReducedMotion();

  const pain = [
    {
      num: '01',
      title: "Context scattered everywhere",
      desc: "Decisions live in Slack threads, Jira tickets, PR comments, and email chains - with no single place to find them.",
      stat: "73%",
      statLabel: "of decisions are undocumented",
    },
    {
      num: '02',
      title: "Knowledge walks out the door",
      desc: "Every time someone leaves, months of institutional context disappears with them. Onboarding starts from zero.",
      stat: "6 weeks",
      statLabel: "average ramp time for new hires",
    },
    {
      num: '03',
      title: "Hours lost re-asking questions",
      desc: "Teams repeat conversations they already had. The same decisions get re-debated because no one can find the original reasoning.",
      stat: "4.5 hrs",
      statLabel: "lost per employee per week",
    },
  ];

  return (
    <section id="story" className="relative py-24 lg:py-32 overflow-hidden bg-white">

      {/* Subtle grid texture */}
      <div className="absolute inset-0 pointer-events-none opacity-[0.035]" style={{
        backgroundImage: 'linear-gradient(#0a3fb5 1px, transparent 1px), linear-gradient(90deg, #0a3fb5 1px, transparent 1px)',
        backgroundSize: '48px 48px',
      }} />

      <div className="relative z-10 max-w-[1100px] mx-auto px-6 lg:px-8">

        {/* Heading */}
        <motion.div initial={reduce ? {} : { opacity: 0, y: 24 }} whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }} transition={{ duration: 0.55 }}
          className="text-center mb-16 max-w-[720px] mx-auto">
          <span className="inline-flex items-center gap-2 px-3 py-1 rounded-full text-[10px] font-semibold uppercase tracking-[0.14em] mb-5 border"
            style={{ fontFamily: FONT_BODY, color: '#0a3fb5', borderColor: 'rgba(10,63,181,0.22)', background: 'rgba(10,63,181,0.06)' }}>
            Knowledge Visibility Gap
          </span>
          <h2 className="text-[clamp(34px,4vw,52px)] font-[700] text-[#1a1c1d] leading-[1.06] tracking-[-0.02em]"
            style={{ fontFamily: FONT_DISPLAY }}>
            The network of your team's knowledge is still invisible.
          </h2>
          <p className="text-[18px] text-[#0a3fb5] leading-[1.45] mt-4 mx-auto max-w-[680px]"
            style={{ fontFamily: FONT_BODY }}>
            Conversations, decisions, and documents are scattered across tools. LoreMind connects the hidden threads into a neural knowledge graph so your team can find the right answer instantly.
          </p>
          <p className="text-[15px] text-[#6b7280] leading-[1.75] mt-5 max-w-[620px] mx-auto" style={{ fontFamily: FONT_BODY }}>
            Without a shared knowledge layer, teams rebuild context from scratch, miss critical signals, and slow down every decision. The graph you see here is the shape of better knowledge flow.
          </p>
        </motion.div>

        {/* Pain cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-5 mb-16">
          {pain.map((p, i) => (
            <motion.div key={i}
              initial={reduce ? {} : { opacity: 0, y: 32 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-60px' }}
              transition={{ duration: 0.5, delay: i * 0.12 }}
              whileHover={{ y: -5 }}
              className="relative rounded-2xl bg-white p-7 flex flex-col gap-4 border border-[rgba(196,197,214,0.25)] transition-shadow duration-300 hover:shadow-[0_20px_48px_rgba(10,63,181,0.09)]"
              style={{ boxShadow: '0 8px 32px rgba(10,63,181,0.05)' }}>
              {/* Top accent bar - blue theme matching the main product style */}
              <div className="absolute top-0 left-6 right-6 h-[2px] rounded-b-full"
                style={{ background: 'linear-gradient(90deg, rgba(10,63,181,0.45), rgba(50,89,206,0.18))' }} />

              <div className="flex items-start justify-between">
                <span className="text-[11px] font-[700] text-[#0a3fb5] tracking-[0.12em] uppercase" style={{ fontFamily: FONT_BODY }}>{p.num}</span>
                <div className="text-right">
                  <div className="text-[26px] font-[700] text-[#1a1c1d] leading-none tracking-[-0.02em]" style={{ fontFamily: FONT_DISPLAY }}>{p.stat}</div>
                  <div className="text-[10px] text-[#6b7280] mt-0.5 max-w-[100px]" style={{ fontFamily: FONT_BODY }}>{p.statLabel}</div>
                </div>
              </div>

              <div>
                <h3 className="text-[16px] font-[700] text-[#1a1c1d] mb-2 leading-[1.3] tracking-[-0.01em]" style={{ fontFamily: FONT_DISPLAY }}>{p.title}</h3>
                <p className="text-[13px] text-[#6b7280] leading-[1.7]" style={{ fontFamily: FONT_BODY }}>{p.desc}</p>
              </div>
            </motion.div>
          ))}
        </div>

        {/* Resolution strip */}
        <motion.div initial={reduce ? {} : { opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }} transition={{ duration: 0.5, delay: 0.2 }}
          className="relative rounded-2xl px-8 py-6 flex flex-col sm:flex-row items-center justify-between gap-6 border border-[rgba(10,63,181,0.12)]"
          style={{ background: 'linear-gradient(135deg, rgba(10,63,181,0.04) 0%, rgba(50,89,206,0.06) 100%)' }}>
          <div className="flex items-center gap-4">
            <div>

              <p className="text-[14px] font-[700] text-[#1a1c1d]" style={{ fontFamily: FONT_DISPLAY }}>LoreMind fixes this passively.</p>
              <p className="text-[12px] text-[#6b7280] mt-0.5" style={{ fontFamily: FONT_BODY }}>No new habits. No manual tagging. Knowledge captured from tools your team already uses.</p>
            </div>
          </div>
          <div className="flex items-center gap-5 shrink-0">
            {['No searching.', 'No guesswork.', 'No lost context.'].map((item, i) => (
              <div key={i} className="flex items-center gap-1.5">
                <Check size={13} className="text-[#0a3fb5] shrink-0" />
                <span className="text-[12px] font-medium text-[#1a1c1d] whitespace-nowrap" style={{ fontFamily: FONT_BODY }}>{item}</span>
              </div>
            ))}
          </div>
        </motion.div>

      </div>
    </section>
  );
};

const HowWeBuiltIt: React.FC = () => {
  const reduce = useReducedMotion();
  const sectionRef = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({ target: sectionRef, offset: ["start 0.5", "end 0.5"] });
  const lineHeight = useTransform(scrollYProgress, [0, 1], ['0%', '100%']);

  const steps = [
    { icon: <Plug2 size={22} />, title: "Multi-tool integration", desc: "Native connectors for Slack, GitHub, Gmail, Jira, Notion, Confluence, and Linear. OAuth-based, pull-synced continuously. First month completely free." },
    { icon: <Brain size={22} />, title: "Passive AI indexing", desc: "LORE Engine indexes new content using fine-tuned embeddings. Every message is semantically understood, not keyword-matched." },
    { icon: <GitMerge size={22} />, title: "Entity resolution", desc: "Unifies identities across tools into one entity with full context history across every source." },
    { icon: <Lock size={22} />, title: "On-premise architecture", desc: "Single Docker Compose file. Designed for air-gapped environments from day one." },
    { icon: <MessageCircle size={22} />, title: "Natural language query", desc: "Semantic vector search plus structured metadata filters. Ask in plain English." },
    { icon: <TrendingUp size={22} />, title: "Decision traceability", desc: "Every answer includes the exact source. Trace why any decision was made, who made it, and when." },
    { icon: <Zap size={22} />, title: "Sub-400ms retrieval", desc: "Graph relationships precomputed at index time. Always fast regardless of data volume." },
    { icon: <Sparkles size={22} />, title: "LORE Engine", desc: "Builds a graph of people, decisions, topics, and tools. Learns how your company actually works." },
  ];

  return (
    <section ref={sectionRef} id="how-we-built" className="relative py-24 lg:py-40 bg-white overflow-hidden">
      <div className="absolute inset-0 pointer-events-none opacity-[0.04]" style={{
        backgroundImage: 'linear-gradient(#0a3fb5 1px, transparent 1px), linear-gradient(90deg, #0a3fb5 1px, transparent 1px)',
        backgroundSize: '60px 60px'
      }} />
      <div className="relative z-10 max-w-[1100px] mx-auto px-6 lg:px-8">
        <motion.div initial={reduce ? {} : { opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }}
          transition={{ duration: 0.5 }} className="text-center mb-20">
          <span className="text-[10px] text-[#0a3fb5] uppercase tracking-[0.16em] block mb-4 font-semibold" style={{ fontFamily: FONT_BODY }}>GUIDE</span>
          <h2 className="text-[clamp(32px,4vw,52px)] font-[700] text-[#1a1c1d] tracking-[-0.02em] uppercase" style={{ fontFamily: FONT_DISPLAY }}>
            How We Achieved This
          </h2>
          <p className="text-[15px] text-[#6b7280] mt-4 max-w-[520px] mx-auto" style={{ fontFamily: FONT_BODY }}>
            Every capability is a deliberate engineering decision.
          </p>
        </motion.div>

        <div className="relative">
          <div className="absolute left-4 lg:left-1/2 lg:-translate-x-1/2 top-2 bottom-2 w-[2px] bg-[rgba(196,197,214,0.4)]">
            <motion.div className="absolute top-0 left-0 w-full bg-[#0a3fb5] origin-top" style={{ height: lineHeight }} />
          </div>

          {steps.map((step, i) => {
            const isLeft = i % 2 === 0;
            return (
              <motion.div key={i}
                initial={reduce ? {} : { opacity: 0, y: 40 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, margin: "-100px" }}
                transition={{ duration: 0.5, ease: 'easeOut' }}
                className={`relative flex items-center mb-10 lg:mb-16 ${isLeft ? 'lg:flex-row' : 'lg:flex-row-reverse'}`}>

                <div className="absolute left-4 lg:left-1/2 -translate-x-1/2 w-4 h-4 sm:w-5 sm:h-5 rounded-full border-[3px] border-[#0a3fb5] bg-[#f9f9fa] z-10 flex items-center justify-center">
                  <div className="w-1.5 h-1.5 rounded-full bg-[#0a3fb5]" />
                </div>

                <div className={`ml-12 sm:ml-20 lg:ml-0 lg:w-[calc(50%-40px)] ${isLeft ? 'lg:mr-auto lg:pr-12' : 'lg:ml-auto lg:pl-12'}`}>
                  <motion.div whileHover={{ y: -4 }}
                    className="group/card p-5 sm:p-8 rounded-2xl bg-white border border-[rgba(196,197,214,0.2)] hover:border-[#0a3fb5]/30 transition-all duration-200"
                    style={{ boxShadow: '0px 16px 40px rgba(10, 63, 181, 0.07)' }}>
                    <div className="flex items-center gap-3 sm:gap-4 mb-3 sm:mb-4">
                      <div className="w-10 h-10 sm:w-12 sm:h-12 rounded-xl flex items-center justify-center bg-[#0a3fb5]/10 text-[#0a3fb5] scale-90 sm:scale-100">{step.icon}</div>
                      <h3 className="text-[17px] sm:text-[19px] font-[700] text-[#1a1c1d] tracking-[-0.01em]" style={{ fontFamily: FONT_DISPLAY }}>{step.title}</h3>
                    </div>
                    <p className="text-[14px] sm:text-[15px] text-[#6b7280] leading-[1.6] sm:leading-[1.7] transition-all duration-300 group-hover/card:text-[#1a1c1d]" style={{ fontFamily: FONT_BODY }}>{step.desc}</p>
                  </motion.div>
                </div>
              </motion.div>
            );
          })}
        </div>
      </div>
    </section>
  );
};

// ============================================
// SECTION 5 - COMPARISON MATRIX
// ============================================
const ComparisonSection: React.FC = () => {
  const reduce = useReducedMotion();
  const [hoveredRow, setHoveredRow] = useState<number | null>(null);

  const features = [
    { feature: 'Integrations', others: '2-3 tools', loremind: '10+ tools', detail: 'Connect everything: Slack, Jira, GitHub, Gmail, Notion, Drive, Linear, Salesforce' },
    { feature: 'Pricing', others: '$20-50/user/mo', loremind: '$4-6/user/mo', detail: 'Built for real teams, not Fortune 500 budgets' },
    { feature: 'Search type', others: 'Keyword only', loremind: 'Semantic AI', detail: 'Finds meaning, not just matching words' },
    { feature: 'Deployment', others: 'Cloud only', loremind: 'On-premise', detail: 'Your data never leaves your infrastructure' },
    { feature: 'Setup effort', others: 'Manual tagging', loremind: 'Zero config', detail: 'Learns from existing conversations automatically' },
    { feature: 'Source attribution', others: 'No', loremind: 'Every answer', detail: 'Traceable to the exact message, commit, or doc' },
    { feature: 'Sync frequency', others: 'Daily/weekly', loremind: 'Every 15 min', detail: 'Near real-time knowledge sync' },
    { feature: 'Team size', others: '1000+ orgs', loremind: '10-500 teams', detail: 'Optimized for teams that move fast' },
  ];

  return (
    <section className="relative py-16 sm:py-24 lg:py-40 bg-white">
      <div className="max-w-[960px] mx-auto px-4 sm:px-6 lg:px-8">
        <motion.div initial={reduce ? {} : { opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }}
          transition={{ duration: 0.5 }} className="text-center mb-10 sm:mb-16">
          <span className="text-[9px] sm:text-[10px] text-[#6b7280] uppercase tracking-[0.16em] block mb-3 sm:mb-4 font-semibold" style={{ fontFamily: FONT_BODY }}>WHY LOREMIND</span>
          <h2 className="text-[clamp(24px,5vw,44px)] font-[700] text-[#1a1c1d] tracking-[-0.02em]" style={{ fontFamily: FONT_DISPLAY }}>
            Most tools give you search.<br />We give you understanding.
          </h2>
        </motion.div>

        {/* Mobile: Card layout */}
        <div className="md:hidden space-y-4">
          {/* Column headers */}
          <div className="grid grid-cols-2 gap-4 mb-2 px-2">
            <div className="text-center text-[10px] font-bold text-slate-400 uppercase tracking-widest" style={{ fontFamily: FONT_BODY }}>Others</div>
            <div className="text-center text-[10px] font-bold text-blue-600 uppercase tracking-widest" style={{ fontFamily: FONT_BODY }}>LoreMind</div>
          </div>

          {features.map((row, i) => (
            <motion.div
              key={i}
              initial={reduce ? {} : { opacity: 0, y: 12 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.4, delay: i * 0.05 }}
              className="rounded-2xl border border-slate-200 overflow-hidden bg-white shadow-sm"
            >
              <div className="px-5 py-3 bg-slate-50/50 border-b border-slate-100">
                <div className="text-[13px] font-bold text-slate-900" style={{ fontFamily: FONT_BODY }}>{row.feature}</div>
              </div>
              <div className="grid grid-cols-2">
                <div className="p-4 flex flex-col items-center justify-center gap-1.5 border-r border-slate-100">
                  <X size={14} className="text-red-400" />
                  <span className="text-[12px] text-slate-500 font-medium text-center leading-tight" style={{ fontFamily: FONT_BODY }}>{row.others}</span>
                </div>
                <div className="p-4 flex flex-col items-center justify-center gap-1.5 bg-blue-50/30">
                  <Check size={14} className="text-blue-600" strokeWidth={3} />
                  <span className="text-[12px] font-bold text-slate-900 text-center leading-tight" style={{ fontFamily: FONT_BODY }}>{row.loremind}</span>
                </div>
              </div>
            </motion.div>
          ))}
        </div>

        {/* Desktop: Table layout */}
        <div className="hidden md:block overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr style={{ borderBottom: '2px solid #d1d5db', background: '#f9f9fa' }}>
                <th className="text-left p-4 text-[12px] font-semibold text-[#1a1c1d]" style={{ fontFamily: FONT_BODY, width: '30%' }}>Feature</th>
                <th className="text-center p-4 text-[11px] font-semibold text-[#6b7280] uppercase tracking-wider border-l border-r border-[#d1d5db]" style={{ fontFamily: FONT_BODY }}>Others</th>
                <th className="text-center p-4 text-[11px] font-bold text-[#0a3fb5] uppercase tracking-wider" style={{ fontFamily: FONT_BODY, background: 'rgba(10,63,181,0.03)' }}>LoreMind</th>
              </tr>
            </thead>
            <tbody>
              {features.map((row, i) => (
                <motion.tr
                  key={i}
                  initial={reduce ? {} : { opacity: 0, y: 8 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true }}
                  transition={{ duration: 0.4, delay: i * 0.06 }}
                  onMouseEnter={() => setHoveredRow(i)}
                  onMouseLeave={() => setHoveredRow(null)}
                  style={{
                    borderBottom: '1px solid #e5e7eb',
                    background: hoveredRow === i ? '#fafbfc' : 'transparent',
                    transition: 'background-color 0.2s'
                  }}
                >
                  <td className="p-4 text-[13px] font-semibold text-[#1a1c1d]" style={{ fontFamily: FONT_BODY }}>
                    {row.feature}
                  </td>
                  <td className="p-4 text-center border-l border-r border-[#d1d5db]" style={{ background: hoveredRow === i ? '#f3f4f6' : '#fafbfc' }}>
                    <div className="flex items-center justify-center gap-2">
                      <X size={14} className="text-red-400/50" />
                      <span className="text-[13px] text-[#6b7280]" style={{ fontFamily: FONT_BODY }}>{row.others}</span>
                    </div>
                  </td>
                  <td className="p-4 text-center" style={{ background: hoveredRow === i ? 'rgba(10,63,181,0.06)' : 'rgba(10,63,181,0.02)' }}>
                    <div className="flex items-center justify-center gap-2">
                      <Check size={14} className="text-green-500/60" />
                      <span className="text-[13px] font-semibold text-[#1a1c1d]" style={{ fontFamily: FONT_BODY }}>{row.loremind}</span>
                    </div>
                  </td>
                </motion.tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
};

// ============================================
// SECTION 6 - PRICING
// ============================================
const PricingSection: React.FC<{ onContactClick: () => void }> = ({ onContactClick }) => {
  const reduce = useReducedMotion();
  const onPrem = ["Full data ownership", "Use your own LLM API key", "Deploy on your infrastructure", "All integrations included"];
  const tiers = [
    { name: "Standard", desc: "Response within 24h. Fix in 2-3 working days." },
    { name: "Priority", desc: "Response within 4h. Fix in 1 working day." },
    { name: "Critical", desc: "Response within 2h. Fix in 12 working hours." },
  ];
  const sub = ["Everything in On-Premise", "Fully managed AWS hosting", "LLM costs included", "Zero setup", "Automatic updates & scaling", "Priority support included"];

  return (
    <section id="pricing" className="relative py-24 lg:py-40 bg-white">
      <div className="max-w-[820px] mx-auto px-6 lg:px-8">
        <motion.div initial={reduce ? {} : { opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }}
          transition={{ duration: 0.5 }} className="text-center mb-14">
          <span className="text-[10px] text-[#6b7280] uppercase tracking-[0.16em] block mb-4 font-semibold" style={{ fontFamily: FONT_BODY }}>INVESTMENT STRUCTURES</span>
          <h2 className="text-[clamp(28px,3.5vw,44px)] font-[700] text-[#1a1c1d] tracking-[-0.02em]" style={{ fontFamily: FONT_DISPLAY }}>
            Simple, <span className="bg-clip-text text-transparent" style={{ backgroundImage: 'linear-gradient(135deg, #0a3fb5, #3259ce)' }}>transparent</span> pricing
          </h2>
          <p className="text-[15px] text-[#6b7280] mt-3" style={{ fontFamily: FONT_BODY }}>No hidden fees. No enterprise sales call. Just the plan that fits your team.</p>
        </motion.div>

        <div className="flex flex-col md:flex-row gap-6">
          <motion.div initial={reduce ? {} : { opacity: 0, y: 28 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }}
            transition={{ duration: 0.5 }}
            className="flex-1 flex flex-col rounded-[20px] p-9 bg-white" style={{ boxShadow: '0px 20px 40px rgba(10, 63, 181, 0.06)' }}>
            <div className="w-12 h-12 rounded-xl flex items-center justify-center mb-5 bg-[#f3f3f4]">
              <Server size={24} className="text-[#0a3fb5]" />
            </div>
            <h3 className="text-[22px] font-[700] text-[#1a1c1d] mb-2 tracking-[-0.01em]" style={{ fontFamily: FONT_DISPLAY }}>On-Premise</h3>
            <p className="text-[14px] text-[#6b7280] mb-4" style={{ fontFamily: FONT_BODY }}>Your server. Your LLM API key. Full control.</p>
            <div className="flex items-baseline gap-1 mb-6">
              <span className="text-[56px] font-[700] text-[#1a1c1d]" style={{ fontFamily: FONT_DISPLAY }}>$4</span>
              <span className="text-[14px] text-[#6b7280]" style={{ fontFamily: FONT_BODY }}>/user/month</span>
            </div>
            <div className="h-px bg-[rgba(196,197,214,0.2)] my-6" />
            <div className="space-y-3 mb-6">
              {onPrem.map((f, i) => (
                <div key={i} className="flex items-center gap-3">
                  <Check size={16} className="text-green-500/60 shrink-0" />
                  <span className="text-[14px] text-[#6b7280]" style={{ fontFamily: FONT_BODY }}>{f}</span>
                </div>
              ))}
            </div>
            <div className="mb-6">
              <span className="text-[10px] text-[#6b7280] uppercase tracking-[0.12em] block mb-3 font-semibold" style={{ fontFamily: FONT_BODY }}>Support tiers (additional charge)</span>
              {tiers.map((t, i) => (
                <div key={i} className="mb-2">
                  <span className="text-[13px] font-[600] text-[#1a1c1d]" style={{ fontFamily: FONT_BODY }}>{t.name}: </span>
                  <span className="text-[12px] text-[#6b7280]" style={{ fontFamily: FONT_BODY }}>{t.desc}</span>
                </div>
              ))}
            </div>
            <div className="mt-auto">
              <button onClick={onContactClick} className="w-full py-3 text-[14px] font-[600] text-[#0a3fb5] border border-[rgba(10,63,181,0.2)] rounded-[10px] hover:bg-[rgba(10,63,181,0.04)] transition-colors" style={{ fontFamily: FONT_BODY }}>Get started</button>
            </div>
          </motion.div>

          <motion.div initial={reduce ? {} : { opacity: 0, y: 28 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.12 }}
            className="flex-1 flex flex-col rounded-[20px] p-9 relative overflow-hidden text-white"
            style={{ background: 'linear-gradient(135deg, #0a3fb5 0%, #3259ce 100%)', animation: 'pricingGlow 2.5s ease-in-out infinite' }}>
            <div className="absolute top-5 right-5 px-3 py-1 rounded-full text-[10px] font-[700] uppercase tracking-wider bg-amber-400 text-[#1a1c1d]" style={{ fontFamily: FONT_BODY }}>MOST POPULAR</div>
            <div className="w-12 h-12 rounded-xl flex items-center justify-center mb-5" style={{ background: 'rgba(255,255,255,0.15)', border: '1px solid rgba(255,255,255,0.2)' }}>
              <Cloud size={24} className="text-white" />
            </div>
            <h3 className="text-[22px] font-[700] mb-2 tracking-[-0.01em]" style={{ fontFamily: FONT_DISPLAY }}>Subscription</h3>
            <p className="text-[14px] text-white/70 mb-4" style={{ fontFamily: FONT_BODY }}>AWS hosting & LLM services, fully managed by us.</p>
            <div className="flex items-baseline gap-1 mb-6">
              <span className="text-[56px] font-[700]" style={{ fontFamily: FONT_DISPLAY }}>$6</span>
              <span className="text-[14px] text-white/70" style={{ fontFamily: FONT_BODY }}>/user/month</span>
            </div>
            <div className="h-px bg-white/20 my-6" />
            <div className="space-y-3 mb-8">
              {sub.map((f, i) => (
                <div key={i} className="flex items-center gap-3">
                  <Check size={16} className="text-white/80 shrink-0" />
                  <span className="text-[14px] text-white/90" style={{ fontFamily: FONT_BODY }}>{f}</span>
                </div>
              ))}
            </div>
            <div className="mt-auto">
              <button onClick={onContactClick} className="w-full py-3 text-[14px] font-[600] text-[#0a3fb5] bg-white rounded-[10px] hover:bg-white/90 transition-colors" style={{ fontFamily: FONT_BODY }}>Get started</button>
            </div>
          </motion.div>
        </div>
      </div>
      <style>{`
        @keyframes pricingGlow {
          0%, 100% { box-shadow: 0 0 0 rgba(10,63,181,0); }
          50% { box-shadow: 0 0 24px rgba(10,63,181,0.15); }
        }
      `}</style>
    </section>
  );
};

// ============================================
// SECTION 7 - VISION
// ============================================
const FlipCard: React.FC<{ goal: { num: string; front: string; back: string }; delay: number }> = ({ goal, delay }) => {
  const reduce = useReducedMotion();
  return (
    <motion.div initial={reduce ? {} : { opacity: 0, y: 28 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }}
      transition={{ duration: 0.45, delay }}
      className="group cursor-pointer h-[280px]" style={{ perspective: '1000px' }}>
      <div className="relative w-full h-full transition-transform duration-700 group-hover:[transform:rotateY(180deg)]"
        style={{ transformStyle: 'preserve-3d' }}>
        <div className="absolute inset-0 p-7 rounded-2xl bg-[#f3f3f4] hover:shadow-[0_20px_40px_rgba(10,63,181,0.06)] transition-shadow flex flex-col justify-center"
          style={{ backfaceVisibility: 'hidden' }}>
          <div>
            <span className="text-[11px] text-[#6b7280] font-semibold" style={{ fontFamily: FONT_BODY }}>{goal.num}</span>
            <h3 className="text-[17px] font-[700] text-[#1a1c1d] mt-4 leading-[1.35] tracking-[-0.01em]" style={{ fontFamily: FONT_DISPLAY }}>{goal.front}</h3>
          </div>
        </div>
        <div className="absolute inset-0 p-7 rounded-2xl bg-white flex flex-col justify-center"
          style={{ backfaceVisibility: 'hidden', transform: 'rotateY(180deg)', boxShadow: '0px 20px 40px rgba(10, 63, 181, 0.08)' }}>
          <div>
            <span className="text-[11px] text-[#0a3fb5] block mb-4 font-semibold" style={{ fontFamily: FONT_BODY }}>{goal.num}</span>
            <p className="text-[16px] text-[#1a1c1d] leading-[1.5] font-medium" style={{ fontFamily: FONT_BODY }}>{goal.back}</p>
          </div>
        </div>
      </div>
    </motion.div>
  );
};

const VisionSection: React.FC = () => {
  const reduce = useReducedMotion();
  const sectionRef = useRef<HTMLElement>(null);

  const goals = [
    { num: "01", front: "Enterprise-grade knowledge for every team", back: "Not just companies that can afford $50/user/month for tools that require a dedicated admin." },
    { num: "02", front: "Zero knowledge loss on every transition", back: "A world where no new hire ever has to reconstruct what was already known." },
    { num: "03", front: "The org graph as the most valuable asset", back: "Not the product, not the headcount -- the accumulated intelligence of every decision ever made." },
    { num: "04", front: "Knowledge retrieval as fast as memory", back: "The moment between asking a question and getting a sourced answer collapses to under one second." },
    { num: "05", front: "The connective tissue of how teams think", back: "Not another tool people use -- the layer underneath every tool that makes knowledge flow." },
  ];

  return (
    <section ref={sectionRef} className="relative py-24 lg:py-40 bg-white overflow-hidden">
      <div className="absolute inset-0 pointer-events-none opacity-[0.05]" style={{
        backgroundImage: 'linear-gradient(#0a3fb5 1px, transparent 1px), linear-gradient(90deg, #0a3fb5 1px, transparent 1px)',
        backgroundSize: '60px 60px'
      }} />

      <div className="absolute inset-0 pointer-events-none overflow-hidden">
        <motion.div className="absolute w-[420px] h-[420px] rounded-full opacity-[0.18]"
          style={{ background: 'radial-gradient(circle, #0a3fb5, transparent 65%)', top: '-210px', left: '-210px' }}
          animate={{ x: [0, 800, 600, 200, 900, 0], y: [0, 300, 700, 500, 200, 0] }}
          transition={{ duration: 28, repeat: Infinity, ease: 'easeInOut', repeatType: 'loop' }} />
        <motion.div className="absolute w-[320px] h-[320px] rounded-full opacity-[0.14]"
          style={{ background: 'radial-gradient(circle, #3259ce, transparent 65%)', top: '-160px', right: '-160px' }}
          animate={{ x: [0, -500, -200, -700, -100, 0], y: [0, 400, 200, 600, 350, 0] }}
          transition={{ duration: 22, repeat: Infinity, ease: 'easeInOut', repeatType: 'loop', delay: 4 }} />
        <motion.div className="absolute w-[280px] h-[280px] rounded-full opacity-[0.13]"
          style={{ background: 'radial-gradient(circle, #0a3fb5, transparent 65%)', bottom: '-140px', left: '30%' }}
          animate={{ x: [-200, 300, 100, -400, 200, -200], y: [0, -300, -500, -200, -400, 0] }}
          transition={{ duration: 25, repeat: Infinity, ease: 'easeInOut', repeatType: 'loop', delay: 8 }} />
      </div>

      <div className="relative z-10 max-w-[1280px] mx-auto px-6 lg:px-8">
        <motion.div initial={reduce ? {} : { opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }}
          transition={{ duration: 0.5 }} className="mb-12 sm:mb-16 max-w-[820px]">
          <span className="text-[10px] text-[#0a3fb5] uppercase tracking-[0.16em] block mb-4 font-semibold" style={{ fontFamily: FONT_BODY }}>OUR VISION</span>
          <h2 className="text-[clamp(28px,4.5vw,56px)] font-[700] text-[#1a1c1d] leading-[1.15] sm:leading-[1.1] tracking-[-0.02em]" style={{ fontFamily: FONT_DISPLAY }}>
            We didn&apos;t start counting<br className="hidden sm:block" /> the day we launched.
          </h2>
          <p className="text-[clamp(28px,4.5vw,56px)] font-[700] leading-[1.15] sm:leading-[1.1] bg-clip-text text-transparent tracking-[-0.02em]"
            style={{ fontFamily: FONT_DISPLAY, backgroundImage: 'linear-gradient(135deg, #0a3fb5, #3259ce)' }}>
            We started the day you did.
          </p>
          <p className="text-[14px] sm:text-[16px] text-[#6b7280] mt-5 sm:mt-6 max-w-[580px] leading-[1.6] sm:leading-[1.8]" style={{ fontFamily: FONT_BODY }}>
            Our story isn&apos;t about when we were incorporated. It&apos;s about when your first team member stopped losing time.
          </p>
        </motion.div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 sm:gap-5 mb-5">
          {goals.slice(0, 3).map((goal, i) => (
            <FlipCard key={i} goal={goal} delay={i * 0.13} />
          ))}
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 sm:gap-5 lg:w-2/3 lg:mx-auto">
          {goals.slice(3).map((goal, i) => (
            <FlipCard key={i + 3} goal={goal} delay={(i + 3) * 0.13} />
          ))}
        </div>

        <motion.div initial={reduce ? {} : { opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }}
          transition={{ duration: 0.5, delay: 0.3 }} className="mt-12 sm:mt-16 max-w-[560px] mx-auto text-center">
          <span className="text-[60px] sm:text-[80px] text-[#0a3fb5]/15 leading-none block" style={{ fontFamily: FONT_DISPLAY }}>&ldquo;</span>
          <p className="text-[clamp(16px,2.2vw,24px)] font-[700] text-[#1a1c1d] leading-[1.5] -mt-6 sm:-mt-10 tracking-[-0.01em]" style={{ fontFamily: FONT_DISPLAY }}>
            We didn&apos;t start when we came.<br />We started when you started.
          </p>
          <div className="w-8 sm:w-10 h-[2px] bg-[#0a3fb5] mt-4 sm:mt-5 mx-auto" />
          <span className="text-[10px] sm:text-[11px] text-[#6b7280] mt-3 block uppercase tracking-wider" style={{ fontFamily: FONT_BODY }}>&mdash; LoreMind</span>
        </motion.div>
      </div>
    </section>
  );
};

// ============================================
// SECTION 8 - FAQ
// ============================================
const FAQItem: React.FC<{ question: string; answer: string; isOpen: boolean; onClick: () => void; index: number }> = ({ question, answer, isOpen, onClick, index }) => {
  const reduce = useReducedMotion();
  return (
    <motion.div initial={reduce ? {} : { opacity: 0, y: 12 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }}
      transition={{ duration: 0.35, delay: index * 0.06 }}
      className="rounded-xl overflow-hidden hover:shadow-[0_8px_24px_rgba(10,63,181,0.06)] transition-shadow bg-white"
      style={{ boxShadow: '0px 2px 8px rgba(10, 63, 181, 0.04)' }}>
      <button onClick={onClick} className="w-full flex items-center justify-between p-5 text-left">
        <span className="text-[15px] font-medium text-[#1a1c1d] pr-4" style={{ fontFamily: FONT_BODY }}>{question}</span>
        <motion.div animate={{ rotate: isOpen ? 45 : 0 }} transition={{ duration: 0.2 }}>
          <Plus size={18} className="text-[#6b7280] shrink-0" />
        </motion.div>
      </button>
      <AnimatePresence>
        {isOpen && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.3, ease: 'easeInOut' }}>
            <div className="px-5 pb-5">
              <p className="text-[14px] text-[#6b7280] leading-[1.7]" style={{ fontFamily: FONT_BODY }}>{answer}</p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
};

const FAQSection: React.FC = () => {
  const reduce = useReducedMotion();
  const [openIndex, setOpenIndex] = useState<number | null>(null);
  const faqs = [
    { question: "How is LoreMind different from a traditional Wiki?", answer: "Wikis require manual documentation and maintenance. LoreMind passively captures knowledge from your existing tools -- Slack, GitHub, Jira, email -- and makes it queryable without anyone writing a single doc. Knowledge stays current automatically." },
    { question: "Does it read private messages?", answer: "No. LoreMind only indexes channels and conversations that your team admin explicitly grants access to via OAuth. Private DMs are never accessed." },
    { question: "How quickly can we get started?", answer: "Your first month is completely free. Setup takes under an hour - run one Docker command, connect your tools via OAuth, and LoreMind begins indexing immediately." },
    { question: "How secure is my data?", answer: "Your data never leaves your infrastructure. LoreMind runs entirely on-premise within a self-contained contextual runtime that isolates every layer of knowledge processing. We designed for air-gapped environments from day one." },
    { question: "What tools does LoreMind integrate with?", answer: "Slack, GitHub, Gmail, Jira, Notion, Confluence, Linear, Google Drive, Salesforce, and Zoom. All integrations use official read-only APIs with OAuth authentication." },
    { question: "Can I use my own LLM API key?", answer: "Yes. The On-Premise plan uses your own LLM API key. The Subscription plan includes LLM costs in the per-user pricing." },
  ];

  return (
    <section id="faq" className="relative py-24 lg:py-40 bg-white">
      <div className="max-w-[640px] mx-auto px-6 lg:px-8">
        <motion.div initial={reduce ? {} : { opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }}
          transition={{ duration: 0.5 }} className="text-center mb-14">
          <h2 className="text-[clamp(28px,3vw,40px)] font-[700] text-[#1a1c1d] tracking-[-0.02em]" style={{ fontFamily: FONT_DISPLAY }}>
            Frequently Asked Questions
          </h2>
        </motion.div>
        <div className="space-y-3">
          {faqs.map((faq, i) => (
            <FAQItem key={i} {...faq} index={i} isOpen={openIndex === i}
              onClick={() => setOpenIndex(openIndex === i ? null : i)} />
          ))}
        </div>
      </div>
    </section>
  );
};

// ============================================
// SECTION 9 - FINAL CTA
// ============================================
const FinalCTA: React.FC<{ onContactClick: () => void }> = ({ onContactClick }) => {
  const reduce = useReducedMotion();
  return (
    <section className="relative py-32 lg:py-40 overflow-hidden bg-white">
      <div className="absolute inset-0" style={{
        background: 'radial-gradient(ellipse at 30% 50%, rgba(10,63,181,0.06) 0%, transparent 65%)',
      }} />
      <div className="absolute inset-0 pointer-events-none overflow-hidden">
        <motion.div className="absolute w-[500px] h-[500px] rounded-full"
          style={{ background: 'radial-gradient(circle, rgba(10,63,181,0.07), transparent 65%)', top: '-100px', right: '-100px' }}
          animate={{ scale: [1, 1.15, 1], x: [0, -60, 0], y: [0, 40, 0] }}
          transition={{ duration: 12, repeat: Infinity, ease: 'easeInOut' }} />
        <motion.div className="absolute w-[360px] h-[360px] rounded-full"
          style={{ background: 'radial-gradient(circle, rgba(50,89,206,0.07), transparent 65%)', bottom: '-80px', left: '20%' }}
          animate={{ scale: [1, 1.2, 1], x: [0, 80, 0], y: [0, -50, 0] }}
          transition={{ duration: 16, repeat: Infinity, ease: 'easeInOut', delay: 3 }} />
      </div>
      <div className="relative z-10 max-w-[1100px] mx-auto px-6 lg:px-8 text-left">
        <motion.div initial={reduce ? {} : { opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }}
          transition={{ duration: 0.5 }} className="max-w-[820px]">
          <h2 className="text-[clamp(34px,4.5vw,56px)] font-[700] text-[#1a1c1d] leading-[1.1] mb-2 tracking-[-0.02em]" style={{ fontFamily: FONT_DISPLAY }}>
            Your company already knows<br />more than it can access.
          </h2>
          <p className="text-[clamp(34px,4.5vw,56px)] font-[700] leading-[1.1] bg-clip-text text-transparent tracking-[-0.02em]"
            style={{ fontFamily: FONT_DISPLAY, backgroundImage: 'linear-gradient(135deg, #0a3fb5, #3259ce)' }}>
            Let&apos;s change that.
          </p>
        </motion.div>
        <motion.div initial={reduce ? {} : { opacity: 0, y: 16 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }}
          transition={{ duration: 0.5, delay: 0.15 }} className="flex flex-wrap gap-5 mt-10">
          <button onClick={onContactClick} className="px-8 py-4 text-[15px] font-[600] text-white rounded-xl hover:shadow-lg transition-all"
            style={{ fontFamily: FONT_BODY, background: 'linear-gradient(135deg, #0a3fb5, #3259ce)' }}>
            Schedule a Demo
          </button>
        </motion.div>
      </div>
    </section>
  );
};

// ============================================
// PARTNERSHIP SECTION
// ============================================
const integrationRows = [
  [
    { name: 'Slack',       img: '/images/slack.png' },
    { name: 'GitHub',      img: '/images/github.png' },
    { name: 'Gmail',       img: '/images/gmail.png' },
    { name: 'Jira',        img: '/images/jira.jpg' },
    { name: 'Google Meet', img: '/images/google_meet.png' },
    { name: 'Outlook',     img: '/images/outlook.png' },
    { name: 'MS Teams',    img: '/images/teams.png' },
    { name: 'Zoho',        img: '/images/zoho.jpg' },
  ],
  [
    { name: 'MS Teams',    img: '/images/teams.png' },
    { name: 'Zoho',        img: '/images/zoho.jpg' },
    { name: 'Google Meet', img: '/images/google_meet.png' },
    { name: 'Outlook',     img: '/images/outlook.png' },
    { name: 'Slack',       img: '/images/slack.png' },
    { name: 'Jira',        img: '/images/jira.jpg' },
    { name: 'GitHub',      img: '/images/github.png' },
    { name: 'Gmail',       img: '/images/gmail.png' },
  ],
];

const IntegrationPill: React.FC<{ name: string; img: string }> = ({ name, img }) => (
  <div
    className="flex items-center gap-3 px-5 py-2.5 rounded-full border border-[rgba(196,197,214,0.35)] mx-3 shrink-0 bg-white shadow-sm"
  >
    {/* Circular logo */}
    <div
      className="w-8 h-8 rounded-full flex items-center justify-center overflow-hidden shrink-0 bg-white border border-[rgba(196,197,214,0.25)]"
    >
      <img src={img} alt={name} className="w-6 h-6 object-contain" />
    </div>
    <span className="text-[13px] font-medium text-[#1a1c1c] whitespace-nowrap" style={{ fontFamily: FONT_BODY }}>
      {name}
    </span>
  </div>
);

const PartnershipSection: React.FC = () => {
  const shouldReduceMotion = useReducedMotion();

  return (
    <section className="bg-white py-20 lg:py-24 overflow-hidden">
      <div className="max-w-[1280px] mx-auto px-6 lg:px-8 text-center mb-12">
        <motion.div
          initial={shouldReduceMotion ? {} : { opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.4 }}
        >
          <div className="text-[11px] text-[#6b7280] uppercase tracking-[0.12em] mb-3" style={{ fontFamily: FONT_BODY }}>
            INTEGRATIONS
          </div>
          <h2 className="text-[clamp(26px,3.2vw,40px)] font-bold text-[#1a1c1c] leading-[1.15] mb-3" style={{ fontFamily: FONT_DISPLAY }}>
            Works where your team already works
          </h2>
          <p className="text-[16px] text-[#6b7280] max-w-[520px] mx-auto leading-[1.7]" style={{ fontFamily: FONT_BODY }}>
            Connect LoreMind to every tool in your stack. No migrations, no new workflows - just instant knowledge retrieval across all of them.
          </p>
        </motion.div>
      </div>

      {/* Marquee rows */}
      <div className="space-y-5">
        {integrationRows.map((row, rowIdx) => (
          <div
            key={rowIdx}
            className="relative flex overflow-hidden"
            style={{ maskImage: 'linear-gradient(to right, transparent, black 10%, black 90%, transparent)' }}
          >
            <div className={`flex ${rowIdx === 1 ? 'animate-marquee-reverse' : 'animate-marquee'}`}>
              {[...row, ...row].map((item, i) => (
                <IntegrationPill key={i} {...item} />
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* Trust note */}
      <motion.div
        initial={shouldReduceMotion ? {} : { opacity: 0 }}
        whileInView={{ opacity: 1 }}
        viewport={{ once: true }}
        transition={{ duration: 0.4, delay: 0.2 }}
        className="text-center mt-6 sm:mt-8 px-4"
      >
        <span className="text-[11px] sm:text-[12px] text-[#6b7280] uppercase tracking-wider inline-block" style={{ fontFamily: FONT_BODY }}>
          Read-only API access · No data stored in the cloud · GDPR-ready
        </span>
      </motion.div>
    </section>
  );
};

// ============================================
// TESTIMONIALS SECTION
// ============================================
interface Testimonial {
  company: string;
  logo: string;
  quote: string;
  author: string;
  role: string;
  metric: string;
  metricLabel: string;
}

const testimonialData: Testimonial[] = [
  {
    company: 'Ezo',
    logo: '/images/testimonials/ezobooks.jpg',
    quote: 'When our lead architect moved on, we were worried about losing 18 months of design context. LoreMind had quietly captured every decision, trade-off, and discussion. The handoff was the smoothest we have ever had.',
    author: 'Gaurav Kate',
    role: 'Founder',
    metric: '18 mo',
    metricLabel: 'of context retained',
  },
  {
    company: 'Daily Opinion',
    logo: '/images/testimonials/daily_opinion.jpg',
    quote: 'In a fast-moving newsroom, every editorial call has to be traceable. LoreMind indexes every brief, source debate, and angle discussion - we can audit any decision in seconds.',
    author: 'Vikas Yadav',
    role: 'Editor-in-Chief',
    metric: '100%',
    metricLabel: 'editorial traceability',
  },
  {
    company: 'Aagamin',
    logo: '/images/testimonials/aagamin.jpg',
    quote: 'Engineers used to spend 30-40 minutes hunting through Slack and Jira just to understand the reasoning behind a past decision. Now that context surfaces instantly - with the source thread attached.',
    author: 'Sajan Nair',
    role: 'Co-founder & CTO',
    metric: '~35 min',
    metricLabel: 'saved per query',
  },
  {
    company: 'Baazar Deal',
    logo: '/images/testimonials/baazar_deal.jpg',
    quote: 'Our PMs were constantly interrupting engineers for historical context. LoreMind ended that overnight. PMs self-serve, engineers stay in flow, and every decision is traceable back to the original discussion.',
    author: 'Poonam Yadav',
    role: 'Director of Product',
    metric: '< 1 wk',
    metricLabel: 'to clear ROI',
  },
];

const TestimonialCard: React.FC<{ t: Testimonial }> = ({ t }) => (
  <div
    className="bg-white rounded-2xl flex flex-col overflow-hidden h-full transition-all duration-300 hover:shadow-[0_20px_48px_-8px_rgba(10,63,181,0.14)] hover:border-[rgba(10,63,181,0.22)]"
    style={{
      border: '1px solid rgba(10,63,181,0.1)',
      boxShadow: '0 4px 20px -4px rgba(10,63,181,0.05)',
    }}
  >
    <div className="flex flex-col flex-1 p-7">
      {/* Top row: logo + company + metric */}
      <div className="flex items-start justify-between gap-4 mb-5">
        <div className="flex items-center gap-3 min-w-0">
          <div
            className="w-12 h-12 rounded-xl overflow-hidden shrink-0 bg-white flex items-center justify-center"
            style={{ border: '1px solid rgba(196,197,214,0.35)' }}
          >
            <img src={t.logo} alt={`${t.company} logo`} className="w-full h-full object-cover" loading="lazy" />
          </div>
          <div className="min-w-0">
            <div className="text-[9px] text-[#6b7280] uppercase tracking-[0.14em] mb-0.5 font-[600]" style={{ fontFamily: FONT_BODY }}>Early Partner</div>
            <div className="text-[16px] font-[700] text-[#0a3fb5] truncate" style={{ fontFamily: FONT_DISPLAY }}>{t.company}</div>
          </div>
        </div>
        <div className="text-right shrink-0">
          <div
            className="text-[20px] font-[800] leading-none bg-clip-text text-transparent"
            style={{ fontFamily: FONT_DISPLAY, backgroundImage: 'linear-gradient(135deg, #0a3fb5 0%, #3259ce 100%)' }}
          >
            {t.metric}
          </div>
          <div className="text-[10px] text-[#6b7280] mt-1 whitespace-nowrap" style={{ fontFamily: FONT_BODY }}>{t.metricLabel}</div>
        </div>
      </div>

      {/* Quote mark */}
      <div className="text-[52px] font-[700] text-[#0a3fb5] leading-none mb-2 opacity-[0.12] select-none" style={{ fontFamily: FONT_DISPLAY }}>&ldquo;</div>

      {/* Quote text */}
      <p className="text-[14px] text-[#374151] leading-[1.75] flex-1" style={{ fontFamily: FONT_BODY }}>{t.quote}</p>

      {/* Author */}
      <div className="flex items-center justify-between gap-3 mt-6 pt-5 border-t border-[rgba(10,63,181,0.06)]">
        <div className="min-w-0">
          <div className="text-[14px] font-[600] text-[#1a1c1c] truncate" style={{ fontFamily: FONT_DISPLAY }}>{t.author}</div>
          <div className="text-[12px] text-[#6b7280] mt-0.5 truncate" style={{ fontFamily: FONT_BODY }}>{t.role}</div>
        </div>
        <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full shrink-0"
          style={{ background: 'rgba(10,63,181,0.05)', border: '1px solid rgba(10,63,181,0.1)' }}>
          <div className="w-1.5 h-1.5 rounded-full bg-[#0a3fb5]" />
          <span className="text-[9px] text-[#0a3fb5] font-[600] uppercase tracking-[0.1em]" style={{ fontFamily: FONT_BODY }}>Verified</span>
        </div>
      </div>
    </div>
  </div>
);

const TestimonialsSection: React.FC = () => {
  const shouldReduceMotion = useReducedMotion();

  return (
    <section className="bg-white py-24 lg:py-32 overflow-hidden">
      <div className="max-w-[1180px] mx-auto px-6 lg:px-8">
        <motion.div
          initial={shouldReduceMotion ? {} : { opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
          className="text-center mb-14"
        >
          <div className="text-[10px] text-[#0a3fb5] uppercase tracking-[0.16em] mb-4 font-[600]" style={{ fontFamily: FONT_BODY }}>
            CUSTOMER STORIES
          </div>
          <h2
            className="text-[clamp(28px,3.8vw,48px)] font-[700] leading-[1.12] tracking-[-0.02em]"
            style={{ fontFamily: FONT_DISPLAY }}
          >
            <span className="text-[#1a1c1c]">Real results from </span>
            <span className="bg-clip-text text-transparent"
              style={{ backgroundImage: 'linear-gradient(135deg, #0a3fb5 0%, #3259ce 100%)' }}>
              real companies
            </span>
            <span className="text-[#1a1c1c]">.</span>
          </h2>
          <p className="text-[15px] text-[#6b7280] mt-4 max-w-[560px] mx-auto" style={{ fontFamily: FONT_BODY }}>
            Teams using LoreMind in production today - across SaaS, media, and commerce.
          </p>
        </motion.div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {testimonialData.map((t, i) => (
            <motion.div
              key={t.company}
              initial={shouldReduceMotion ? {} : { opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-60px' }}
              transition={{ duration: 0.5, delay: i * 0.08, ease: [0.22, 1, 0.36, 1] }}
            >
              <TestimonialCard t={t} />
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
};

// ============================================
// VIDEO DEMO SECTION
// ============================================
const VideoDemoSection: React.FC = () => {
  const shouldReduceMotion = useReducedMotion();
  return (
    <section className="bg-white py-20 lg:py-28 overflow-hidden">
      <div className="max-w-[1100px] mx-auto px-6 lg:px-8">
        <motion.div
          initial={shouldReduceMotion ? {} : { opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
          className="text-center mb-10"
        >
          <div className="text-[10px] text-[#0a3fb5] uppercase tracking-[0.16em] mb-4 font-[600]" style={{ fontFamily: FONT_BODY }}>
            SEE IT IN ACTION
          </div>
          <h2
            className="text-[clamp(28px,3.6vw,44px)] font-[700] leading-[1.12] tracking-[-0.02em] text-[#1a1c1d]"
            style={{ fontFamily: FONT_DISPLAY }}
          >
            Watch LoreMind in under two minutes.
          </h2>
        </motion.div>
        <motion.div
          initial={shouldReduceMotion ? {} : { opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-80px' }}
          transition={{ duration: 0.6, delay: 0.1, ease: [0.22, 1, 0.36, 1] }}
          className="relative rounded-2xl overflow-hidden bg-black"
          style={{
            aspectRatio: '16 / 9',
            boxShadow: '0 30px 80px -20px rgba(10,63,181,0.25), 0 10px 30px -8px rgba(10,63,181,0.12)',
            border: '1px solid rgba(10,63,181,0.12)',
          }}
        >
          <iframe
            src="https://www.youtube-nocookie.com/embed/UpLJvw6byGc?rel=0&modestbranding=1&vq=hd1080&hd=1"
            title="LoreMind product demo"
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
            allowFullScreen
            loading="lazy"
            className="absolute inset-0 w-full h-full"
            style={{ border: 0 }}
          />
        </motion.div>
      </div>
    </section>
  );
};

// ============================================
// FOOTER
// ============================================
const LandingFooter: React.FC = () => {
  const socials = [
    { Icon: Twitter, href: 'https://x.com/Loremind_forge', label: 'X (Twitter)' },
    { Icon: Linkedin, href: 'https://www.linkedin.com/company/loremind-forge-private-limited/posts/?feedView=all', label: 'LinkedIn' },
    { Icon: Mail, href: 'mailto:founders@loremind.in', label: 'Email' },
  ];
  return (
    <footer id="footer" className="border-t border-[rgba(196,197,214,0.2)] pt-16 pb-12 bg-[#f9f9fa]">
      <div className="max-w-[1280px] mx-auto px-6 lg:px-8">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-12">
          <div>
            <span className="text-[20px] font-[700] bg-clip-text text-transparent"
              style={{ fontFamily: FONT_DISPLAY, backgroundImage: 'linear-gradient(135deg, #002b86 0%, #0a3fb5 60%, #3259ce 100%)' }}>LoreMind</span>
            <p className="text-[14px] text-[#6b7280] mt-2 max-w-[200px] leading-[1.65]" style={{ fontFamily: FONT_BODY }}>Your company&apos;s memory.</p>
            <div className="flex gap-2 mt-6">
              {socials.map(({ Icon, href, label }) => (
                <a key={label} href={href} target={href.startsWith('mailto:') ? undefined : '_blank'} rel={href.startsWith('mailto:') ? undefined : 'noopener noreferrer'} aria-label={label}
                  className="w-9 h-9 flex items-center justify-center rounded-lg bg-white text-[#6b7280] hover:text-[#0a3fb5] transition-all"
                  style={{ boxShadow: '0 2px 8px rgba(10,63,181,0.04)' }}>
                  <Icon size={16} />
                </a>
              ))}
            </div>
          </div>
          <div>
            <h4 className="text-[13px] font-[700] text-[#1a1c1d] mb-4" style={{ fontFamily: FONT_DISPLAY }}>Product</h4>
            <div className="space-y-2">
              {[
                { name: 'How it works', href: '#how-we-built' },
                { name: 'Features', href: '#values' },
                { name: 'Pricing', href: '#pricing' },
                { name: 'Integrations', href: '#how-we-built' },
                { name: 'Security', href: '#faq' },
              ].map(link => (
                <a key={link.name} href={link.href} className="block text-[13px] text-[#6b7280] hover:text-[#1a1c1d] transition-colors py-1" style={{ fontFamily: FONT_BODY }}>{link.name}</a>
              ))}
            </div>
          </div>
          <div>
            <h4 className="text-[13px] font-[700] text-[#1a1c1d] mb-4" style={{ fontFamily: FONT_DISPLAY }}>Company</h4>
            <div className="space-y-2">
              {['About', 'Blog', 'Careers', 'Contact'].map(link => (
                <a key={link} href="#" className="block text-[13px] text-[#6b7280] hover:text-[#1a1c1d] transition-colors py-1" style={{ fontFamily: FONT_BODY }}>{link}</a>
              ))}
              <a href="mailto:founders@loremind.in" className="flex items-center gap-2 text-[13px] text-[#6b7280] hover:text-[#0a3fb5] transition-colors py-1" style={{ fontFamily: FONT_BODY }}>
                <Mail size={14} className="text-[#0a3fb5]" /> founders@loremind.in
              </a>
            </div>
          </div>
        </div>
        <div className="mt-12 pt-6 border-t border-[rgba(196,197,214,0.2)] flex flex-col md:flex-row justify-between items-center gap-4">
          <span className="text-[11px] text-[#6b7280] uppercase tracking-wider" style={{ fontFamily: FONT_BODY }}>&copy; 2026 LoreMind Technologies. Based in India.</span>
          <div className="flex gap-6">
            {['Privacy Policy', 'Terms of Service'].map(link => (
              <a key={link} href="#" className="text-[13px] text-[#6b7280] hover:text-[#1a1c1d] transition-colors" style={{ fontFamily: FONT_BODY }}>{link}</a>
            ))}
          </div>
        </div>
      </div>
    </footer>
  );
};

// ============================================
// MAIN LANDING PAGE
// ============================================
export default function NewLandingPage() {
  const [contactOpen, setContactOpen] = useState(false);
  const openContact = () => setContactOpen(true);

  useEffect(() => {
    const handleAnchorClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      const anchor = target.closest('a[href^="#"]');
      if (anchor) {
        const href = anchor.getAttribute('href');
        if (href && href !== '#') {
          e.preventDefault();
          const element = document.querySelector(href);
          if (element) {
            element.scrollIntoView({ behavior: 'smooth' });
          }
        }
      }
    };
    document.addEventListener('click', handleAnchorClick);
    return () => document.removeEventListener('click', handleAnchorClick);
  }, []);

  return (
    <div className="min-h-screen bg-white new-landing-page">
      <Navigation onContactClick={openContact} />
      <main>
        {false && <AnimatedBackgroundCircles />}
        {false && <StorySection />}
        <HeroSection onContactClick={openContact} />
        <PartnershipSection />
        <StorylineAnimation onContactClick={openContact} />
        <ValueSection />
        <VideoDemoSection />
        <TestimonialsSection />
        <HowWeBuiltIt />
        <ComparisonSection />
        <PricingSection onContactClick={openContact} />
        <VisionSection />
        <FAQSection />
        <FinalCTA onContactClick={openContact} />
      </main>
      <LandingFooter />
      <ContactModal open={contactOpen} onClose={() => setContactOpen(false)} />
    </div>
  );
}