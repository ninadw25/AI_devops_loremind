import { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useNavigate } from 'react-router-dom';

// Total duration of the learning animation in ms - keep it snappy.
const TOTAL_DURATION = 2600;

const STAGES = [
  'Connecting your workspace…',
  'Indexing recent conversations…',
  'Extracting decisions and context…',
  'Building your knowledge graph…',
  'Ready.',
];

export default function Learning() {
  const navigate = useNavigate();
  const [progress, setProgress] = useState(0);
  const [stageIndex, setStageIndex] = useState(0);

  // Progress animation
  useEffect(() => {
    const tick = 20; // ms
    const step = (tick / TOTAL_DURATION) * 100;
    const id = setInterval(() => {
      setProgress(p => {
        const next = p + step;
        if (next >= 100) {
          clearInterval(id);
          return 100;
        }
        return next;
      });
    }, tick);
    return () => clearInterval(id);
  }, []);

  // Stage message rotation - synced to progress
  useEffect(() => {
    const idx = Math.min(STAGES.length - 1, Math.floor((progress / 100) * STAGES.length));
    if (idx !== stageIndex) setStageIndex(idx);
  }, [progress, stageIndex]);

  // Navigate when complete
  useEffect(() => {
    if (progress >= 100) {
      const t = setTimeout(() => navigate('/ask'), 260);
      return () => clearTimeout(t);
    }
  }, [progress, navigate]);

  return (
    <div className="min-h-screen flex items-center justify-center px-6 relative overflow-hidden"
      style={{ background: 'radial-gradient(ellipse at center, #f8fafc 0%, #eef2ff 100%)' }}
    >
      {/* Subtle dot grid background */}
      <div
        className="absolute inset-0 opacity-[0.35] pointer-events-none"
        style={{
          backgroundImage: 'radial-gradient(circle at 1px 1px, rgba(10,63,181,0.12) 1px, transparent 0)',
          backgroundSize: '32px 32px',
        }}
      />

      {/* Soft ambient glow */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background: 'radial-gradient(circle at 50% 50%, rgba(10,63,181,0.06) 0%, transparent 55%)',
        }}
      />

      <div className="relative z-10 flex flex-col items-center">
        {/* Orbit / logo */}
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.35, ease: 'easeOut' }}
          className="relative mb-10"
        >
          <div className="relative w-[160px] h-[160px]">
            {/* Outer rotating ring */}
            <motion.div
              className="absolute inset-0 rounded-full"
              style={{
                background: `conic-gradient(from 0deg, rgba(10,63,181,0) 0%, rgba(10,63,181,0.6) ${progress}%, rgba(10,63,181,0) ${Math.min(100, progress + 1)}%)`,
                padding: '2px',
                maskImage: 'radial-gradient(circle, transparent 68%, black 70%)',
                WebkitMaskImage: 'radial-gradient(circle, transparent 68%, black 70%)',
              }}
              animate={{ rotate: 360 }}
              transition={{ duration: 3, repeat: Infinity, ease: 'linear' }}
            />

            {/* Static outer stroke */}
            <div className="absolute inset-0 rounded-full border border-slate-200/70" />

            {/* Inner pulsing disc */}
            <motion.div
              className="absolute inset-[18px] rounded-full"
              style={{
                background: 'radial-gradient(circle at 40% 35%, rgba(255,255,255,0.95), rgba(226,232,240,0.7))',
                boxShadow: '0 10px 30px -10px rgba(10,63,181,0.25), inset 0 1px 2px rgba(255,255,255,0.9)',
              }}
              animate={{
                boxShadow: [
                  '0 10px 30px -10px rgba(10,63,181,0.25), inset 0 1px 2px rgba(255,255,255,0.9)',
                  '0 14px 40px -10px rgba(10,63,181,0.40), inset 0 1px 2px rgba(255,255,255,0.9)',
                  '0 10px 30px -10px rgba(10,63,181,0.25), inset 0 1px 2px rgba(255,255,255,0.9)',
                ],
              }}
              transition={{ duration: 1.6, repeat: Infinity, ease: 'easeInOut' }}
            />

            {/* Logo */}
            <div className="absolute inset-0 flex items-center justify-center">
              <motion.img
                src="/images/logo.png"
                alt="Loremind"
                className="w-14 h-14 object-contain"
                animate={{ scale: [1, 1.05, 1] }}
                transition={{ duration: 1.6, repeat: Infinity, ease: 'easeInOut' }}
              />
            </div>
          </div>
        </motion.div>

        {/* Headline */}
        <motion.h1
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.35, delay: 0.1 }}
          className="text-[26px] font-semibold text-slate-900 text-center leading-tight tracking-[-0.01em]"
          style={{ fontFamily: 'var(--font-display)' }}
        >
          Setting up your knowledge brain
        </motion.h1>

        {/* Dynamic stage line */}
        <div className="h-6 mt-2 flex items-center justify-center">
          <AnimatePresence mode="wait">
            <motion.p
              key={stageIndex}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.25 }}
              className="text-[13px] text-slate-500"
            >
              {STAGES[stageIndex]}
            </motion.p>
          </AnimatePresence>
        </div>

        {/* Progress bar */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.3, delay: 0.15 }}
          className="mt-8 w-[320px]"
        >
          <div className="relative h-[5px] bg-slate-200/70 rounded-full overflow-hidden">
            <motion.div
              className="absolute inset-y-0 left-0 rounded-full"
              style={{
                background: 'linear-gradient(90deg, #0a3fb5 0%, #3259ce 100%)',
                boxShadow: '0 0 10px rgba(10,63,181,0.3)',
              }}
              animate={{ width: `${progress}%` }}
              transition={{ duration: 0.08, ease: 'linear' }}
            />
          </div>
          <div className="mt-2.5 flex items-center justify-between text-[10px] font-medium text-slate-400 uppercase tracking-wider">
            <span>Step {Math.min(STAGES.length, stageIndex + 1)} of {STAGES.length}</span>
            <span className="tabular-nums">{Math.round(progress)}%</span>
          </div>
        </motion.div>

        {/* Stage dots */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.3, delay: 0.2 }}
          className="mt-6 flex items-center gap-2"
        >
          {STAGES.map((_, i) => {
            const active = i <= stageIndex;
            return (
              <div
                key={i}
                className={`transition-all duration-300 rounded-full ${
                  active ? 'w-6 h-1.5 bg-blue-600' : 'w-1.5 h-1.5 bg-slate-300'
                }`}
              />
            );
          })}
        </motion.div>
      </div>
    </div>
  );
}
