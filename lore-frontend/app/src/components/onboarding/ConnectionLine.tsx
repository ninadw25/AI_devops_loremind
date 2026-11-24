import { motion } from 'framer-motion';
import { calculateConnectionPath, type Point } from '@/utils/svgPaths';

interface ConnectionLineProps {
  start: Point;
  end: Point;
  isConnected: boolean;
  isHovered?: boolean;
}

export const ConnectionLine = ({ start, end, isConnected, isHovered = false }: ConnectionLineProps) => {
  const path = calculateConnectionPath(start, end);

  return (
    <motion.path
      d={path}
      stroke={isConnected ? "hsl(217, 91%, 60%)" : "hsl(220, 13%, 69%)"}
      strokeWidth="2"
      fill="none"
      strokeLinecap="round"
      initial={{ pathLength: 1, opacity: 0.3 }}
      animate={{
        pathLength: 1,
        opacity: isConnected ? 1 : isHovered ? 0.5 : 0.3,
        strokeWidth: isConnected ? 3 : 2
      }}
      transition={{
        opacity: { duration: 0.3 },
        strokeWidth: { duration: 0.3 },
        stroke: { duration: 0.5 }
      }}
      filter={isConnected ? "url(#connectionGlow)" : undefined}
      style={{
        pointerEvents: 'none'
      }}
    />
  );
};
