import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';

interface UseLearningTimerReturn {
  progress: number;
  phase: 'learning' | 'acquired';
}

/**
 * Hook to manage the 10-second learning timer
 * Auto-navigates to dashboard when complete
 */
export const useLearningTimer = (duration: number = 10000): UseLearningTimerReturn => {
  const [progress, setProgress] = useState(0);
  const [phase, setPhase] = useState<'learning' | 'acquired'>('learning');
  const navigate = useNavigate();

  useEffect(() => {
    const intervalMs = 100; // Update every 100ms for smooth animation
    const incrementPerTick = 100 / (duration / intervalMs);

    const interval = setInterval(() => {
      setProgress(prev => {
        const next = prev + incrementPerTick;

        // Change phase at 95%
        if (next >= 95 && phase === 'learning') {
          setPhase('acquired');
        }

        // Complete and navigate
        if (next >= 100) {
          clearInterval(interval);
          setTimeout(() => navigate('/dashboard'), 1000);
          return 100;
        }

        return next;
      });
    }, intervalMs);

    return () => clearInterval(interval);
  }, [duration, navigate, phase]);

  return { progress, phase };
};
