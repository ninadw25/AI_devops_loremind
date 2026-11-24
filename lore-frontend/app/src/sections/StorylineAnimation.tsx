import React from 'react';
import { motion } from 'framer-motion';

const FONT_DISPLAY = "var(--font-display)";
const FONT_BODY    = "var(--font-body)";

// ─── Outline glyphs (default/filler state) ───────────────────────────────────
const G = {
  Slack: ({ s = 22 }: { s?: number }) => (
    <svg width={s} height={s} viewBox="0 0 20 20" fill="none">
      <rect x="2" y="8" width="5" height="5" rx="1.5" stroke="currentColor" strokeWidth="1.5"/>
      <rect x="8" y="2" width="5" height="5" rx="1.5" stroke="currentColor" strokeWidth="1.5" transform="rotate(90,10.5,4.5)"/>
      <rect x="8" y="8" width="5" height="5" rx="1.5" stroke="currentColor" strokeWidth="1.5"/>
      <rect x="14" y="8" width="4" height="4" rx="1" stroke="currentColor" strokeWidth="1.3"/>
      <line x1="4.5" y1="3" x2="4.5" y2="8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
      <line x1="10.5" y1="14" x2="10.5" y2="18" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  ),
  Jira: ({ s = 22 }: { s?: number }) => (
    <svg width={s} height={s} viewBox="0 0 20 20" fill="none">
      <rect x="10.5" y="1.5" width="7" height="7" rx="1.1" fill="currentColor"/>
      <rect x="6.5" y="5.5" width="7" height="7" rx="1.1" fill="currentColor"/>
      <rect x="2.5" y="9.5" width="7" height="7" rx="1.1" fill="currentColor"/>
    </svg>
  ),
  GitHub: ({ s = 22 }: { s?: number }) => (
    <svg width={s} height={s} viewBox="0 0 20 20" fill="none">
      <circle cx="10" cy="4"  r="2.2" stroke="currentColor" strokeWidth="1.5"/>
      <circle cx="3"  cy="16" r="2.2" stroke="currentColor" strokeWidth="1.5"/>
      <circle cx="17" cy="16" r="2.2" stroke="currentColor" strokeWidth="1.5"/>
      <path d="M10 6.2v5.5M10 11.7L5.2 14M10 11.7L14.8 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  ),
  GitLab: ({ s = 22 }: { s?: number }) => (
    <svg width={s} height={s} viewBox="0 0 20 20" fill="none">
      <path d="M10 17L3 12l1.8-5.5 1.5 4.5h7.4l1.5-4.5L17 12l-7 5z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"/>
      <path d="M5.3 11l1.5-4.5L8.5 10m6.2 1L13.2 6.5 11.5 10" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  ),
  Mail: ({ s = 22 }: { s?: number }) => (
    <svg width={s} height={s} viewBox="0 0 20 20" fill="none">
      <rect x="2" y="4" width="16" height="12" rx="2" stroke="currentColor" strokeWidth="1.5"/>
      <path d="M2 6l8 6 8-6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  ),
  Teams: ({ s = 22 }: { s?: number }) => (
    <svg width={s} height={s} viewBox="0 0 20 20" fill="none">
      <rect x="1" y="7" width="12" height="11" rx="2" stroke="currentColor" strokeWidth="1.4"/>
      <path d="M7 7V5a2 2 0 014 0v2" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
      <circle cx="15" cy="6" r="2" stroke="currentColor" strokeWidth="1.2"/>
      <path d="M13 11c1.5 0 4 .8 4 3" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
    </svg>
  ),
  Outlook: ({ s = 22 }: { s?: number }) => (
    <svg width={s} height={s} viewBox="0 0 20 20" fill="none">
      <rect x="2" y="4" width="16" height="12" rx="2" stroke="currentColor" strokeWidth="1.4"/>
      <path d="M2 5l8 6 8-6" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
      <circle cx="6" cy="10" r="1.5" stroke="currentColor" strokeWidth="1.2"/>
    </svg>
  ),
  Meet: ({ s = 22 }: { s?: number }) => (
    <svg width={s} height={s} viewBox="0 0 20 20" fill="none">
      <rect x="2" y="6" width="11" height="8" rx="2" stroke="currentColor" strokeWidth="1.5"/>
      <path d="M13 9.5L18 7v6l-5-2V9.5z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
    </svg>
  ),
  Zoho: ({ s = 22 }: { s?: number }) => (
    <svg width={s} height={s} viewBox="-2 2 26 14" fill="none">
      <rect x="0"   y="4" width="9" height="9" rx="2"   stroke="currentColor" strokeWidth="1.5" transform="rotate(-13 4.5 8.5)"/>
      <rect x="5"   y="3" width="9" height="9" rx="2"   stroke="currentColor" strokeWidth="1.5" transform="rotate(-8 9.5 7.5)"/>
      <rect x="10"  y="3" width="9" height="9" rx="2"   stroke="currentColor" strokeWidth="1.5" transform="rotate(-3 14.5 7.5)"/>
      <rect x="15"  y="4" width="9" height="9" rx="2"   stroke="currentColor" strokeWidth="1.5" transform="rotate(2 19.5 8.5)"/>
    </svg>
  ),
};

// ─── Brand logos (back of flip card) ─────────────────────────────────────────
const BrandG = {
  Slack: ({ s = 36 }: { s?: number }) => (
    <svg width={s} height={s} viewBox="0 0 54 54" fill="none">
      <path d="M19.712.133a5.381 5.381 0 00-5.376 5.387 5.381 5.381 0 005.376 5.386h5.376V5.52A5.381 5.381 0 0019.712.133m0 14.365H5.376A5.381 5.381 0 000 19.884a5.381 5.381 0 005.376 5.386h14.336a5.381 5.381 0 005.376-5.386 5.381 5.381 0 00-5.376-5.386" fill="#36C5F0"/>
      <path d="M53.76 19.884a5.381 5.381 0 00-5.376-5.386 5.381 5.381 0 00-5.376 5.386v5.386h5.376a5.381 5.381 0 005.376-5.386M39.39 19.884V5.52A5.381 5.381 0 0034.014.133a5.381 5.381 0 00-5.376 5.387v14.364a5.381 5.381 0 005.376 5.386 5.381 5.381 0 005.376-5.386" fill="#2EB67D"/>
      <path d="M34.014 53.64a5.381 5.381 0 005.376-5.387 5.381 5.381 0 00-5.376-5.386h-5.376v5.386a5.381 5.381 0 005.376 5.387m0-14.365h14.37a5.381 5.381 0 005.376-5.386 5.381 5.381 0 00-5.376-5.387H34.014a5.381 5.381 0 00-5.376 5.387 5.381 5.381 0 005.376 5.386" fill="#ECB22E"/>
      <path d="M0 33.889a5.381 5.381 0 005.376 5.386 5.381 5.381 0 005.376-5.386v-5.387H5.376A5.381 5.381 0 000 33.889m14.336.001v14.364a5.381 5.381 0 005.376 5.386 5.381 5.381 0 005.376-5.386V33.89a5.381 5.381 0 00-5.376-5.387 5.381 5.381 0 00-5.376 5.387" fill="#E01E5A"/>
    </svg>
  ),
  Jira: ({ s = 36 }: { s?: number }) => (
    <svg width={s} height={s} viewBox="0 0 256 256" fill="none">
      <rect x="150" y="20" width="90" height="90" rx="14" fill="#2684FF"/>
      <rect x="83" y="83" width="90" height="90" rx="14" fill="#2684FF"/>
      <rect x="16" y="146" width="90" height="90" rx="14" fill="#2684FF"/>
    </svg>
  ),
  GitHub: ({ s = 36 }: { s?: number }) => (
    <svg width={s} height={s} viewBox="0 0 16 16" fill="none">
      <path fillRule="evenodd" clipRule="evenodd" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" fill="#1A1C1D"/>
    </svg>
  ),
  Gmail: ({ s = 36 }: { s?: number }) => (
    <svg width={s} height={s} viewBox="0 0 52 40" fill="none">
      <path d="M4 40h8V20L0 11v25a4 4 0 004 4z" fill="#4285F4"/>
      <path d="M40 40h8a4 4 0 004-4V11l-12 9v20z" fill="#34A853"/>
      <path d="M40 4v16l12-9V6a4 4 0 00-4-4 4 4 0 00-2 .7L40 4z" fill="#FBBC04"/>
      <path d="M12 20V4l14 10L40 4v16L26 30 12 20z" fill="#EA4335"/>
      <path d="M0 6v5l12 9V4L6 .7A4 4 0 004 0a4 4 0 00-4 4v2z" fill="#C5221F"/>
    </svg>
  ),
  Meet: ({ s = 36 }: { s?: number }) => {
    const uid = React.useId().replace(/[^a-z0-9]/gi, '');
    return (
      <svg width={s} height={s} viewBox="0 0 56 48" fill="none">
        <defs>
          <clipPath id={`gm${uid}`}>
            <rect x="1" y="2" width="34" height="44" rx="7"/>
          </clipPath>
        </defs>
        <rect x="1"  y="2"  width="17" height="22" fill="#1A73E8" clipPath={`url(#gm${uid})`}/>
        <rect x="18" y="2"  width="17" height="22" fill="#EA4335" clipPath={`url(#gm${uid})`}/>
        <rect x="1"  y="24" width="17" height="22" fill="#FBBC04" clipPath={`url(#gm${uid})`}/>
        <rect x="18" y="24" width="17" height="22" fill="#34A853" clipPath={`url(#gm${uid})`}/>
        <path d="M37 14l17-8v36l-17-8V14z" fill="#188038"/>
      </svg>
    );
  },
  Zoho: ({ s = 36 }: { s?: number }) => (
    <svg width={s} height={s} viewBox="-8 0 238 106" fill="none">
      <rect x="5"   y="12" width="72" height="72" rx="16" stroke="#E8382D" strokeWidth="11" transform="rotate(-14 41 48)"/>
      <rect x="53"  y="7"  width="72" height="72" rx="16" stroke="#1DA462" strokeWidth="11" transform="rotate(-9  89 43)"/>
      <rect x="101" y="6"  width="72" height="72" rx="16" stroke="#2970C1" strokeWidth="11" transform="rotate(-4 137 42)"/>
      <rect x="149" y="11" width="72" height="72" rx="16" stroke="#F5A623" strokeWidth="11" transform="rotate(1  185 47)"/>
    </svg>
  ),
};

// ─── Card types ──────────────────────────────────────────────────────────────
type FlipCardDef = {
  kind: 'flip';
  name: string;
  Glyph: (p: { s?: number }) => React.ReactElement;
  Brand: (p: { s?: number }) => React.ReactElement;
  quote: string;
  problem: string;
  fix: string;
};

type FillerCardDef = {
  kind: 'filler';
  name: string;
  Glyph: (p: { s?: number }) => React.ReactElement;
};

type CardDef = FlipCardDef | FillerCardDef;

// ─── Column layout with 6 flip cards distributed + filler icon cards ─────────
const GRID_COLS: CardDef[][] = [
  // Col 0
  [
    { kind: 'flip', name: 'Slack', Glyph: G.Slack, Brand: BrandG.Slack,
      quote: '#general · April 14',
      problem: 'Arjun’s call on tier-2 pricing is buried in a thread from April. He left in October.',
      fix: 'Every thread indexed to its decision-maker. Ask “why $49?” - get the reasoning, author, date.' },
    { kind: 'filler', name: 'Jira', Glyph: G.Jira },
    { kind: 'filler', name: 'Outlook', Glyph: G.Outlook },
  ],
  // Col 1
  [
    { kind: 'filler', name: 'GitHub', Glyph: G.GitHub },
    { kind: 'flip', name: 'Jira', Glyph: G.Jira, Brand: BrandG.Jira,
      quote: 'LG-421 · Won’t fix',
      problem: '“Won’t fix - see Priya.” Priya left six months ago. The bug is back in production.',
      fix: 'Every rejection links to the Slack thread and PR that justified it. The “why” survives.' },
    { kind: 'filler', name: 'Teams', Glyph: G.Teams },
  ],
  // Col 2
  [
    { kind: 'flip', name: 'GitHub', Glyph: G.GitHub, Brand: BrandG.GitHub,
      quote: 'PR #882 · 02:14 AM',
      problem: '“LGTM, trust me.” The reviewer trusted was Rahul. Rahul quit last quarter.',
      fix: 'Every PR stitched to its discussions and tickets. Trust chains outlive the author.' },
    { kind: 'filler', name: 'Zoho', Glyph: G.Zoho },
    { kind: 'filler', name: 'Meet', Glyph: G.Meet },
  ],
  // Col 3
  [
    { kind: 'filler', name: 'Slack', Glyph: G.Slack },
    { kind: 'flip', name: 'Gmail', Glyph: G.Mail, Brand: BrandG.Gmail,
      quote: 'Re: Export feature',
      problem: 'An ex-founder promised Acme a custom export via email. Acme is asking. The archive is 40k threads deep.',
      fix: 'Commitments surfaced by client and topic. Ask “what did we promise Acme?” - get the thread.' },
    { kind: 'filler', name: 'Jira', Glyph: G.Jira },
  ],
  // Col 4
  [
    { kind: 'flip', name: 'Google Meet', Glyph: G.Meet, Brand: BrandG.Meet,
      quote: 'Q3 Roadmap · Oct 2',
      problem: 'Three pivots discussed on a call. No minutes. No recording. The PM quit in October.',
      fix: 'Transcripts auto-parsed for decisions, owners, and deadlines. Every meeting becomes queryable.' },
    { kind: 'filler', name: 'GitLab', Glyph: G.GitLab },
    { kind: 'filler', name: 'Outlook', Glyph: G.Outlook },
  ],
  // Col 5
  [
    { kind: 'filler', name: 'Gmail', Glyph: G.Mail },
    { kind: 'flip', name: 'Zoho CRM', Glyph: G.Zoho, Brand: BrandG.Zoho,
      quote: 'Deal #2489 · Net-90',
      problem: 'A ₹40L deal closed on net-90 terms. Notes say “check with Vikram.” Vikram is in Australia now.',
      fix: 'CRM, email, and Slack threads unified into one deal history. Every rep sees the full story.' },
    { kind: 'filler', name: 'Teams', Glyph: G.Teams },
  ],
];

const COL_MARGIN_TOP = ['5%', '14%', '22%', '22%', '14%', '5%'];

// ─── Filler card (default state only - icon centered) ────────────────────────
const FillerCard: React.FC<{ card: FillerCardDef; isMobile?: boolean }> = ({ card, isMobile }) => (
  <div
    className="rounded-2xl shrink-0 relative overflow-hidden flex items-center justify-center"
    style={{
      height: isMobile ? 100 : 160,
      background: '#f4f4f6',
      border: '1px solid rgba(196,197,214,0.4)',
      boxShadow: '0 1px 3px rgba(0,0,0,0.03)',
    }}
  >
    <span style={{ color: '#b0b5c4' }}><card.Glyph s={isMobile ? 16 : 22} /></span>
  </div>
);

// ─── Flip card (problem → LoreMind fix on hover) ─────────────────────────────
const FlipCard: React.FC<{
  card: FlipCardDef;
  isMobile?: boolean;
  isOpen?: boolean;
  onOpen?: () => void;
  onClose?: () => void;
}> = ({ card, isMobile, isOpen, onOpen, onClose }) => {
  const { name, Glyph, Brand, quote, problem, fix } = card;

  if (isMobile) {
    // Mobile: Vertical accordion-style cards (no flip animation)
    return (
      <div className="rounded-2xl overflow-hidden border border-slate-200">
        {/* Front (Problem) */}
        <div
          className={`p-4 transition-all duration-300 ${isOpen ? 'hidden' : 'block'}`}
          style={{
            background: 'linear-gradient(180deg, #fffafa 0%, #ffffff 100%)',
            border: '1.5px solid rgba(220, 38, 38, 0.28)',
          }}
        >
          <div className="flex items-start gap-2 mb-3">
            <div className="w-6 h-6 rounded-lg flex items-center justify-center flex-shrink-0"
              style={{ background: 'rgba(220,38,38,0.08)', color: '#b91c1c' }}>
              <Glyph s={14} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-xs font-semibold" style={{ color: '#b91c1c', fontFamily: FONT_BODY }}>
                {name}
              </div>
              <div className="text-[11px]" style={{ color: '#9ca3af', fontFamily: FONT_BODY }}>
                {quote}
              </div>
            </div>
          </div>

          <p className="text-[13px] leading-relaxed mb-3" style={{ color: '#374151', fontFamily: FONT_BODY }}>
            {problem}
          </p>

          <button
            onClick={onOpen}
            className="w-full py-2 rounded-lg font-medium transition-colors"
            style={{
              background: 'rgba(220,38,38,0.08)',
              color: '#b91c1c',
              fontSize: '13px',
              fontFamily: FONT_BODY,
            }}
          >
            See the fix →
          </button>
        </div>

        {/* Back (Solution) */}
        <div
          className={`p-4 transition-all duration-300 ${!isOpen ? 'hidden' : 'block'}`}
          style={{
            background: 'linear-gradient(180deg, #f5f8ff 0%, #ffffff 100%)',
            border: '1.5px solid rgba(10, 63, 181, 0.25)',
          }}
        >
          <div className="flex items-start gap-2 mb-3">
            <div className="w-6 h-6 rounded-lg flex items-center justify-center bg-white flex-shrink-0"
              style={{ border: '1px solid rgba(196,197,214,0.35)' }}>
              <Brand s={14} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-xs font-bold uppercase tracking-wider" style={{ color: '#0a3fb5', fontFamily: FONT_BODY }}>
                LoreMind
              </div>
            </div>
          </div>

          <p className="text-[13px] leading-relaxed mb-3" style={{ color: '#374151', fontFamily: FONT_BODY }}>
            {fix}
          </p>

          <button
            onClick={onClose}
            className="w-full py-2 rounded-lg font-medium transition-colors"
            style={{
              background: 'linear-gradient(135deg, #0a3fb5, #3259ce)',
              color: 'white',
              fontSize: '13px',
              fontFamily: FONT_BODY,
            }}
          >
            Back
          </button>
        </div>
      </div>
    );
  }

  // Desktop: 3D flip animation
  const [flipped, setFlipped] = React.useState(false);

  return (
    <div
      tabIndex={0}
      onMouseEnter={() => setFlipped(true)}
      onMouseLeave={() => setFlipped(false)}
      onFocus={() => setFlipped(true)}
      onBlur={() => setFlipped(false)}
      className="rounded-2xl shrink-0 relative outline-none"
      style={{ perspective: '1400px', height: 310 }}
    >
      <div
        className="relative w-full h-full"
        style={{
          transformStyle: 'preserve-3d',
          transition: 'transform 0.7s cubic-bezier(0.22, 1, 0.36, 1)',
          transform: flipped ? 'rotateY(180deg)' : 'rotateY(0deg)',
        }}
      >
        {/* FRONT - problem */}
        <div
          className="absolute inset-0 rounded-2xl p-4 flex flex-col justify-between"
          style={{
            backfaceVisibility: 'hidden',
            WebkitBackfaceVisibility: 'hidden',
            background: 'linear-gradient(180deg, #fffafa 0%, #ffffff 100%)',
            border: '1.5px solid rgba(220, 38, 38, 0.28)',
            boxShadow: '0 6px 20px rgba(220, 38, 38, 0.05)',
          }}
        >
          <div className="flex items-start justify-between gap-2">
            <div className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0"
              style={{ background: 'rgba(220,38,38,0.08)', color: '#b91c1c' }}>
              <Glyph s={18} />
            </div>
            <span className="text-[8px] font-[700] uppercase tracking-[0.14em] px-1.5 py-[3px] rounded-full flex-shrink-0"
              style={{ fontFamily: FONT_BODY, color: '#b91c1c', background: 'rgba(220,38,38,0.08)', border: '1px solid rgba(220,38,38,0.2)' }}>
              Lost
            </span>
          </div>

          <div className="min-w-0">
            <div className="text-[9px] font-[600] mb-1"
              style={{ fontFamily: FONT_BODY, color: '#b91c1c' }}>
              {name} · {quote}
            </div>
            <p className="text-[12px] leading-[1.55] text-[#1a1c1d]"
              style={{ fontFamily: FONT_BODY }}>
              {problem}
            </p>
          </div>

          <div className="text-[9px] font-[500] text-[#9ca3af] uppercase tracking-[0.12em]"
            style={{ fontFamily: FONT_BODY }}>
            Hover for fix →
          </div>
        </div>

        {/* BACK - LoreMind fix */}
        <div
          className="absolute inset-0 rounded-2xl p-4 flex flex-col justify-between"
          style={{
            backfaceVisibility: 'hidden',
            WebkitBackfaceVisibility: 'hidden',
            transform: 'rotateY(180deg)',
            background: 'linear-gradient(180deg, #f5f8ff 0%, #ffffff 100%)',
            border: '1.5px solid rgba(10, 63, 181, 0.25)',
            boxShadow: '0 6px 20px rgba(10, 63, 181, 0.08)',
          }}
        >
          <div className="flex items-start justify-between gap-2">
            <div className="w-9 h-9 rounded-lg flex items-center justify-center bg-white flex-shrink-0"
              style={{ border: '1px solid rgba(196,197,214,0.35)' }}>
              <Brand s={22} />
            </div>
            <span className="text-[8px] font-[700] uppercase tracking-[0.14em] px-1.5 py-[3px] rounded-full text-white flex-shrink-0"
              style={{ fontFamily: FONT_BODY, background: 'linear-gradient(135deg, #0a3fb5, #3259ce)' }}>
              Preserved
            </span>
          </div>

          <div className="min-w-0">
            <div className="text-[9px] font-[700] uppercase tracking-[0.12em] mb-1"
              style={{ fontFamily: FONT_BODY, color: '#0a3fb5' }}>
              With LoreMind
            </div>
            <p className="text-[12px] leading-[1.55] text-[#1a1c1d]"
              style={{ fontFamily: FONT_BODY }}>
              {fix}
            </p>
          </div>

          <div className="flex items-center gap-1.5 text-[9px] font-[600] uppercase tracking-[0.12em]"
            style={{ fontFamily: FONT_BODY, color: '#0a3fb5' }}>
            <svg width="8" height="8" viewBox="0 0 12 12" fill="none">
              <path d="M2 6l3 3 5-6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
            <span>Captured · Cited</span>
          </div>
        </div>
      </div>
    </div>
  );
};

const MobileFlipCard: React.FC<{ card: FlipCardDef }> = ({ card }) => {
  const [isOpen, setIsOpen] = React.useState(false);
  return (
    <FlipCard
      card={card}
      isMobile={true}
      isOpen={isOpen}
      onOpen={() => setIsOpen(true)}
      onClose={() => setIsOpen(false)}
    />
  );
};

// ─── Main export ─────────────────────────────────────────────────────────────
export const StorylineAnimation: React.FC<{ onContactClick: () => void }> = ({ onContactClick }) => {
  void onContactClick;
  const [isMobile, setIsMobile] = React.useState(window.innerWidth < 640);

  React.useEffect(() => {
    const handleResize = () => setIsMobile(window.innerWidth < 640);
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  // Flatten all cards into a single array for mobile view
  const allCards = GRID_COLS.flat();
  const flipCards = allCards.filter(c => c.kind === 'flip');

  return (
    <section className="relative bg-white py-16 sm:py-20 md:py-0 md:min-h-screen md:overflow-hidden">
      {/* Headline block */}
      <div className={`relative z-20 flex flex-col items-center text-center px-4 sm:px-6 ${isMobile ? 'mb-20 pt-4' : 'absolute inset-x-0'}`}
        style={!isMobile ? { top: '6vh', pointerEvents: 'none' } : {}}>
        <motion.span
          initial={{ opacity: 0, y: 8 }} whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-80px' }}
          transition={{ duration: 0.4, ease: [0.4, 0, 0.2, 1] }}
          className="text-[9px] sm:text-[10px] uppercase tracking-[0.16em] block mb-3 sm:mb-4 font-semibold"
          style={{ fontFamily: FONT_BODY, color: '#b91c1c' }}>
          The Problem
        </motion.span>
        <motion.h2 className="font-[700] tracking-[-0.03em] leading-[1.1]"
          style={{ fontFamily: FONT_DISPLAY, fontSize: 'clamp(28px,5vw,54px)', color: '#1a1c1d', maxWidth: 760 }}
          initial={{ opacity: 0, y: 12 }} whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-80px' }}
          transition={{ duration: 0.5, delay: 0.1, ease: [0.4, 0, 0.2, 1] }}>
          <span style={{
            background: 'linear-gradient(135deg,#dc2626,#b91c1c)',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
            backgroundClip: 'text',
          }}>Scattered knowledge</span>{' '}across channels.
        </motion.h2>

        <motion.p className="mt-3 sm:mt-4 max-w-[560px] text-[13px] sm:text-[15px] leading-[1.6] sm:leading-[1.65]"
          style={{ fontFamily: FONT_BODY, color: '#6b7280' }}
          initial={{ opacity: 0, y: 8 }} whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-80px' }}
          transition={{ duration: 0.5, delay: 0.25, ease: [0.4, 0, 0.2, 1] }}>
          Your star employee quit - and took a decade of context with them.
          Every tool holds a piece. None of them talks to the next.
        </motion.p>
      </div>

      {/* Mobile: Interactive card stack */}
      {isMobile && (
        <div className="relative z-10 max-w-4xl mx-auto px-4 pb-20">
          <div className="grid grid-cols-1 gap-4">
            {flipCards.map((card, i) => (
              <MobileFlipCard key={i} card={card as FlipCardDef} />
            ))}
          </div>
        </div>
      )}

      {/* Desktop: Original 6-column staggered grid */}
      {!isMobile && (
        <>
          <div className="absolute inset-x-0 flex gap-2 px-2" style={{ top: '25vh', bottom: '-6vh' }}>
            {GRID_COLS.map((col, ci) => (
              <div key={ci} className="flex-1 flex flex-col gap-2"
                style={{ marginTop: COL_MARGIN_TOP[ci] }}>
                {col.map((card, ki) =>
                  card.kind === 'flip'
                    ? <FlipCard key={ki} card={card as FlipCardDef} />
                    : <FillerCard key={ki} card={card as FillerCardDef} />
                )}
              </div>
            ))}
          </div>

          {/* Top + bottom fades so the grid feels like it flows in/out */}
          <div className="absolute inset-x-0 top-0 z-10 pointer-events-none"
            style={{ height: '30vh', background: 'linear-gradient(to bottom,#ffffff 50%,transparent 100%)' }} />
          <div className="absolute inset-x-0 bottom-0 z-10 pointer-events-none"
            style={{ height: '22vh', background: 'linear-gradient(to top,#ffffff 30%,transparent 100%)' }} />
        </>
      )}
    </section>
  );
};
