import { motion } from 'framer-motion';

interface RevolvingLoaderProps {
  progress: number; // 0-100
  size?: number;
  strokeWidth?: number;
}

export const RevolvingLoader = ({ progress, size = 280, strokeWidth = 4 }: RevolvingLoaderProps) => {
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;

  return (
    <svg
      width={size}
      height={size}
      className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2"
      style={{ transform: 'translate(-50%, -50%) rotate(-90deg)' }}
    >
      {/* Background circle (faint) */}
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        stroke="hsl(214, 32%, 91%)"
        strokeWidth={strokeWidth}
        fill="none"
        opacity="0.3"
      />

      {/* Revolving progress arc */}
      <motion.circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        stroke="hsl(217, 91%, 60%)"
        strokeWidth={strokeWidth}
        fill="none"
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={circumference * (1 - progress / 100)}
        animate={{
          rotate: 360
        }}
        transition={{
          rotate: {
            duration: 2,
            repeat: Infinity,
            ease: "linear"
          }
        }}
        style={{
          transformOrigin: 'center',
          filter: 'drop-shadow(0 0 8px rgba(59, 130, 246, 0.6))'
        }}
      />
    </svg>
  );
};
