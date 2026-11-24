import { useState, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useNavigate } from 'react-router-dom';
import { Sparkles, Check, AlertCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface Integration {
  id: string;
  name: string;
  icon: string;
  connected: boolean;
}

const leftApps = ['github', 'gmail', 'jira'];
const rightApps = ['slack', 'outlook', 'meet'];

// Connection Line Component
const ConnectionLine = ({
  isConnected,
  isAnimating,
  side
}: {
  isConnected: boolean;
  isAnimating: boolean;
  side: 'left' | 'right';
}) => {
  const pathD = side === 'left'
    ? "M0 50 Q60 50 100 50"
    : "M100 50 Q40 50 0 50";

  return (
    <svg
      width="100"
      height="100"
      viewBox="0 0 100 100"
      className="absolute top-1/2 -translate-y-1/2"
      style={{
        [side]: side === 'left' ? 'calc(100% + 8px)' : 'calc(100% + 8px)',
        width: '120px',
        height: '60px'
      }}
    >
      {/* Background line (grey) */}
      <path
        d={pathD}
        stroke="#e2e8f0"
        strokeWidth="2"
        fill="none"
        strokeLinecap="round"
      />

      {/* Active line (blue) */}
      <motion.path
        d={pathD}
        stroke="#3b82f6"
        strokeWidth="2"
        fill="none"
        strokeLinecap="round"
        initial={{ pathLength: 0 }}
        animate={{
          pathLength: isConnected ? 1 : 0,
          stroke: isAnimating ? ["#3b82f6", "#60a5fa", "#93c5fd", "#3b82f6"] : "#3b82f6"
        }}
        transition={{
          pathLength: { duration: 0.5, ease: "easeOut" },
          stroke: { duration: 0.8, repeat: isAnimating ? Infinity : 0 }
        }}
      />

      {/* Flow animation dots */}
      {isConnected && isAnimating && (
        <circle
          r="3"
          fill="#60a5fa"
          filter="url(#glow)"
          className="flow-dot"
          style={{
            offsetPath: `path('${pathD}')`,
            animation: 'flowMove 1s linear infinite',
          }}
        />
      )}

      <defs>
        <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="2" result="coloredBlur"/>
          <feMerge>
            <feMergeNode in="coloredBlur"/>
            <feMergeNode in="SourceGraphic"/>
          </feMerge>
        </filter>
      </defs>
    </svg>
  );
};

// App Icon Component
const AppIcon = ({
  app,
  isConnected,
  onToggle,
  isAnimating
}: {
  app: Integration;
  isConnected: boolean;
  onToggle: () => void;
  isAnimating: boolean;
}) => {
  return (
    <motion.button
      onClick={onToggle}
      disabled={isAnimating}
      className={`
        relative w-16 h-16 rounded-2xl flex items-center justify-center
        transition-all duration-300 ease-out
        ${isConnected
          ? 'shadow-glow ring-2 ring-blue-400 ring-offset-2'
          : 'shadow-app hover:shadow-app-hover'
        }
        ${isAnimating ? 'animate-pulse-glow' : ''}
      `}
      style={{
        background: isConnected
          ? 'linear-gradient(135deg, rgba(59, 130, 246, 0.1) 0%, rgba(147, 197, 253, 0.15) 100%)'
          : 'white'
      }}
      whileHover={{ scale: isAnimating ? 1 : 1.05 }}
      whileTap={{ scale: isAnimating ? 1 : 0.95 }}
      animate={isConnected ? {
        boxShadow: [
          "0 0 20px rgba(59, 130, 246, 0.4), 0 0 40px rgba(59, 130, 246, 0.2)",
          "0 0 30px rgba(59, 130, 246, 0.6), 0 0 60px rgba(59, 130, 246, 0.4)",
          "0 0 20px rgba(59, 130, 246, 0.4), 0 0 40px rgba(59, 130, 246, 0.2)"
        ]
      } : {}}
      transition={{ duration: 2, repeat: isConnected ? Infinity : 0 }}
    >
      <img
        src={app.icon}
        alt={app.name}
        className="w-10 h-10 object-contain"
      />

      {/* Connected indicator */}
      <AnimatePresence>
        {isConnected && (
          <motion.div
            initial={{ scale: 0, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0, opacity: 0 }}
            className="absolute -top-1 -right-1 w-5 h-5 bg-blue-500 rounded-full flex items-center justify-center"
          >
            <Check className="w-3 h-3 text-white" strokeWidth={3} />
          </motion.div>
        )}
      </AnimatePresence>
    </motion.button>
  );
};

export default function Onboarding() {
  const navigate = useNavigate();
  const [isAnimating, setIsAnimating] = useState(false);
  const [showError, setShowError] = useState(false);
  const [apps, setApps] = useState<Integration[]>([
    { id: 'github', name: 'GitHub', icon: '/images/github.png', connected: false },
    { id: 'gmail', name: 'Gmail', icon: '/images/gmail.png', connected: false },
    { id: 'jira', name: 'Jira', icon: '/images/jira.jpg', connected: false },
    { id: 'slack', name: 'Slack', icon: '/images/slack.png', connected: false },
    { id: 'outlook', name: 'Outlook', icon: '/images/outlook.png', connected: false },
    { id: 'meet', name: 'Google Meet', icon: '/images/google_meet.png', connected: false },
  ]);

  const connectedApps = apps.filter(app => app.connected);
  const hasMinimumConnection = connectedApps.length >= 1;

  const toggleAppConnection = (appId: string) => {
    setApps(prev =>
      prev.map(app =>
        app.id === appId ? { ...app, connected: !app.connected } : app
      )
    );
  };

  const handleContinue = useCallback(() => {
    if (!hasMinimumConnection) {
      setShowError(true);
      setTimeout(() => setShowError(false), 3000);
      return;
    }

    setIsAnimating(true);

    // Persist connected sources for downstream screens (AskLore filters by these)
    localStorage.setItem(
      'loremind_connected_sources',
      JSON.stringify(connectedApps.map(a => a.id))
    );

    // Navigate to loading page after animation
    setTimeout(() => {
      navigate('/learning');
    }, 2500);
  }, [hasMinimumConnection, navigate, connectedApps]);

  // Handle Enter key press
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Enter' && !isAnimating) {
        handleContinue();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleContinue, isAnimating]);

  const leftSideApps = apps.filter(app => leftApps.includes(app.id));
  const rightSideApps = apps.filter(app => rightApps.includes(app.id));

  return (
    <div className="min-h-screen flex flex-col items-center justify-center px-4 py-12">
      {/* Header */}
      <motion.div
        className="text-center mb-16"
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6 }}
      >
        <motion.div
          className="inline-flex items-center gap-2 mb-4"
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.2 }}
        >
          <Sparkles className="w-5 h-5 text-blue-500" />
          <span className="text-sm font-medium text-blue-600 uppercase tracking-wider">Step 1 of 2</span>
        </motion.div>

        <h1 className="text-4xl md:text-5xl font-bold text-slate-900 mb-4">
          Connect Your Knowledge
        </h1>
        <p className="text-lg text-slate-500 max-w-md mx-auto">
          Link your sources to build the knowledge brain
        </p>
      </motion.div>

      {/* Main Connection Visualization */}
      <motion.div
        className="relative flex items-center justify-center gap-16 md:gap-24 lg:gap-32 mb-16"
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.6, delay: 0.3 }}
      >
        {/* Left Side Apps */}
        <div className="flex flex-col gap-6">
          {leftSideApps.map((app, index) => (
            <motion.div
              key={app.id}
              className="relative"
              initial={{ opacity: 0, x: -30 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.4 + index * 0.1 }}
            >
              <AppIcon
                app={app}
                isConnected={app.connected}
                onToggle={() => toggleAppConnection(app.id)}
                isAnimating={isAnimating}
              />
              {/* Connection line */}
              <div className="absolute top-1/2 -translate-y-1/2 left-full">
                <ConnectionLine
                  isConnected={app.connected}
                  isAnimating={isAnimating}
                  side="left"
                />
              </div>
            </motion.div>
          ))}
        </div>

        {/* Center Brain Image */}
        <motion.div
          className="relative z-10"
          animate={isAnimating ? {
            scale: [1, 1.05, 1],
          } : {}}
          transition={{ duration: 1, repeat: isAnimating ? Infinity : 0 }}
        >
          <div className={`
            w-36 h-36 rounded-full flex items-center justify-center
            ${isAnimating ? 'bg-blue-50' : 'bg-white'}
            transition-all duration-500
          `}
          style={{
            boxShadow: isAnimating
              ? '0 0 60px rgba(59, 130, 246, 0.4), inset 0 0 40px rgba(59, 130, 246, 0.1)'
              : '0 4px 30px rgba(0, 0, 0, 0.08)'
          }}
          >
            <img
              src="/images/brain.png"
              alt="Knowledge Brain"
              className="w-28 h-28 object-contain rounded-full"
            />
          </div>

          {/* Orbiting particles when animating */}
          {isAnimating && (
            <>
              {[...Array(6)].map((_, i) => (
                <motion.div
                  key={i}
                  className="absolute w-2 h-2 bg-blue-400 rounded-full"
                  style={{
                    top: '50%',
                    left: '50%',
                  }}
                  animate={{
                    x: [0, Math.cos(i * Math.PI / 3) * 80, 0],
                    y: [0, Math.sin(i * Math.PI / 3) * 80, 0],
                    opacity: [0, 1, 0],
                    scale: [0.5, 1, 0.5],
                  }}
                  transition={{
                    duration: 2,
                    repeat: Infinity,
                    delay: i * 0.2,
                    ease: "easeInOut",
                  }}
                />
              ))}
            </>
          )}
        </motion.div>

        {/* Right Side Apps */}
        <div className="flex flex-col gap-6">
          {rightSideApps.map((app, index) => (
            <motion.div
              key={app.id}
              className="relative"
              initial={{ opacity: 0, x: 30 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.4 + index * 0.1 }}
            >
              {/* Connection line */}
              <div className="absolute top-1/2 -translate-y-1/2 right-full">
                <ConnectionLine
                  isConnected={app.connected}
                  isAnimating={isAnimating}
                  side="right"
                />
              </div>
              <AppIcon
                app={app}
                isConnected={app.connected}
                onToggle={() => toggleAppConnection(app.id)}
                isAnimating={isAnimating}
              />
            </motion.div>
          ))}
        </div>
      </motion.div>

      {/* Connection Status */}
      <motion.div
        className="mb-8"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.7 }}
      >
        <div className="flex items-center gap-3 text-sm">
          <div className={`
            w-3 h-3 rounded-full transition-colors duration-300
            ${hasMinimumConnection ? 'bg-blue-500' : 'bg-slate-300'}
          `} />
          <span className={hasMinimumConnection ? 'text-blue-600 font-medium' : 'text-slate-400'}>
            {connectedApps.length} {connectedApps.length === 1 ? 'source' : 'sources'} connected
          </span>
          <span className="text-slate-300">|</span>
          <span className="text-slate-400">
            At least 1 required
          </span>
        </div>
      </motion.div>

      {/* Error Message */}
      <AnimatePresence>
        {showError && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            className="mb-4 flex items-center gap-2 text-amber-600 bg-amber-50 px-4 py-2 rounded-lg"
          >
            <AlertCircle className="w-4 h-4" />
            <span className="text-sm font-medium">Please connect at least one source to continue</span>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Continue Button */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.8 }}
      >
        <Button
          onClick={handleContinue}
          disabled={isAnimating}
          className={`
            px-12 py-6 text-lg font-semibold rounded-xl
            transition-all duration-300
            ${hasMinimumConnection
              ? 'bg-blue-600 hover:bg-blue-700 text-white shadow-lg hover:shadow-xl hover:shadow-blue-500/25'
              : 'bg-slate-200 text-slate-400 cursor-not-allowed'
            }
          `}
        >
          {isAnimating ? (
            <span className="flex items-center gap-2">
              <motion.span
                animate={{ rotate: 360 }}
                transition={{ duration: 1, repeat: Infinity, ease: "linear" }}
              >
                <Sparkles className="w-5 h-5" />
              </motion.span>
              Connecting...
            </span>
          ) : (
            'Connect'
          )}
        </Button>
      </motion.div>

      {/* Keyboard hint */}
      <motion.p
        className="mt-4 text-sm text-slate-400"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 1 }}
      >
        Press <kbd className="px-2 py-1 bg-slate-100 rounded text-slate-600 font-mono text-xs">Enter</kbd> to continue
      </motion.p>
    </div>
  );
}
