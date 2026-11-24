import { motion } from 'framer-motion';
import { useMemo } from 'react';

interface KnowledgeParticlesProps {
  brainCenter: { x: number; y: number };
  count?: number;
}

export const KnowledgeParticles = ({ brainCenter, count = 40 }: KnowledgeParticlesProps) => {
  // Generate particles once on mount
  const particles = useMemo(() => {
    // Reduce particle count on mobile for performance
    const isMobile = typeof window !== 'undefined' && window.innerWidth < 768;
    const particleCount = isMobile ? 20 : count;

    return Array.from({ length: particleCount }, (_, i) => ({
      id: i,
      // Start from edges of viewport
      startX: Math.random() * window.innerWidth,
      startY: Math.random() * window.innerHeight,
      delay: Math.random() * 2,
      duration: 2 + Math.random() * 1.5
    }));
  }, [count]);

  return (
    <div className="absolute inset-0 pointer-events-none overflow-hidden">
      {particles.map((particle) => (
        <motion.div
          key={particle.id}
          className="absolute w-1.5 h-1.5 rounded-full bg-blue-400"
          style={{
            left: particle.startX,
            top: particle.startY
          }}
          animate={{
            x: [0, brainCenter.x - particle.startX],
            y: [0, brainCenter.y - particle.startY],
            scale: [1, 0],
            opacity: [0.8, 0]
          }}
          transition={{
            duration: particle.duration,
            delay: particle.delay,
            repeat: Infinity,
            ease: "easeIn"
          }}
        />
      ))}
    </div>
  );
};
