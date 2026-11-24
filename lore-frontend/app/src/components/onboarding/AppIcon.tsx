import { motion } from 'framer-motion';
import { Check, Loader2 } from 'lucide-react';
import { useState } from 'react';

interface AppIconProps {
  logo: string;
  name: string;
  description: string;
  position: { x: number; y: number };
  connected: boolean;
  connecting: boolean;
  onClick: () => void;
  onHoverChange: (hovered: boolean) => void;
}

export const AppIcon = ({
  logo,
  name,
  description,
  position,
  connected,
  connecting,
  onClick,
  onHoverChange
}: AppIconProps) => {
  const [showTooltip, setShowTooltip] = useState(false);

  const handleMouseEnter = () => {
    setShowTooltip(true);
    onHoverChange(true);
  };

  const handleMouseLeave = () => {
    setShowTooltip(false);
    onHoverChange(false);
  };

  return (
    <motion.div
      className="absolute cursor-pointer"
      style={{
        left: position.x,
        top: position.y,
        transform: 'translate(-50%, -50%)'
      }}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      onClick={!connected && !connecting ? onClick : undefined}
      whileHover={!connected && !connecting ? { scale: 1.05 } : {}}
    >
      {/* App Icon Card */}
      <div
        className={`
          relative w-20 h-20 md:w-24 md:h-24 rounded-2xl flex items-center justify-center
          transition-all duration-300 bg-white
          ${connected
            ? 'shadow-lg shadow-green-500/20 border-2 border-green-400'
            : connecting
            ? 'shadow-lg shadow-blue-500/20 border-2 border-blue-400'
            : 'shadow-md hover:shadow-lg border border-slate-200'
          }
        `}
      >
        <img
          src={logo}
          alt={name}
          className={`w-12 h-12 md:w-14 md:h-14 object-contain ${
            connecting ? 'opacity-50' : ''
          }`}
        />

        {/* Status badge */}
        {connected && (
          <motion.div
            className="absolute -top-2 -right-2 w-7 h-7 bg-green-500 rounded-full flex items-center justify-center shadow-lg"
            initial={{ scale: 0 }}
            animate={{ scale: 1 }}
            transition={{ type: "spring", stiffness: 300, damping: 15 }}
          >
            <Check className="w-4 h-4 text-white" />
          </motion.div>
        )}

        {connecting && (
          <div className="absolute -top-2 -right-2 w-7 h-7 bg-blue-500 rounded-full flex items-center justify-center shadow-lg">
            <Loader2 className="w-4 h-4 text-white animate-spin" />
          </div>
        )}
      </div>

      {/* Tooltip */}
      {showTooltip && !connecting && (
        <motion.div
          className="absolute left-1/2 -translate-x-1/2 -top-20 z-50 pointer-events-none"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 10 }}
          transition={{ duration: 0.2 }}
        >
          <div className="bg-slate-900 text-white px-4 py-2 rounded-lg shadow-xl whitespace-nowrap">
            <div className="font-semibold text-sm mb-1">{name}</div>
            <div className="text-xs text-slate-300">{description}</div>
            {/* Arrow */}
            <div className="absolute left-1/2 -translate-x-1/2 -bottom-1 w-2 h-2 bg-slate-900 rotate-45" />
          </div>
        </motion.div>
      )}
    </motion.div>
  );
};
