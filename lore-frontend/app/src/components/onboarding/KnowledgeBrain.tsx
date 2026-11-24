import { motion } from 'framer-motion';

interface BrainNode {
  id: string;
  cx: number;
  cy: number;
  r: number;
  opacity: number;
}

interface Synapse {
  from: string;
  to: string;
}

interface KnowledgeBrainProps {
  isLearning?: boolean;
  className?: string;
  connectedCount?: number;
}

// Neural network structure - interconnected nodes
const BRAIN_NODES: BrainNode[] = [
  // Core node (center)
  { id: 'core', cx: 100, cy: 100, r: 24, opacity: 1 },
  // Upper layer
  { id: 'n1', cx: 75, cy: 75, r: 16, opacity: 0.9 },
  { id: 'n2', cx: 125, cy: 75, r: 16, opacity: 0.9 },
  // Middle layer
  { id: 'n3', cx: 65, cy: 120, r: 14, opacity: 0.85 },
  { id: 'n4', cx: 135, cy: 120, r: 14, opacity: 0.85 },
  // Lower layer
  { id: 'n5', cx: 85, cy: 140, r: 12, opacity: 0.8 },
  { id: 'n6', cx: 115, cy: 140, r: 12, opacity: 0.8 }
];

// Connections between nodes (synapses)
const SYNAPSES: Synapse[] = [
  { from: 'core', to: 'n1' },
  { from: 'core', to: 'n2' },
  { from: 'core', to: 'n3' },
  { from: 'core', to: 'n4' },
  { from: 'n1', to: 'n3' },
  { from: 'n2', to: 'n4' },
  { from: 'n3', to: 'n5' },
  { from: 'n4', to: 'n6' }
];

export const KnowledgeBrain = ({ isLearning = false, className = '', connectedCount = 0 }: KnowledgeBrainProps) => {
  // Create a map for quick node lookups
  const nodeMap = BRAIN_NODES.reduce((acc, node) => {
    acc[node.id] = node;
    return acc;
  }, {} as Record<string, BrainNode>);

  // Activation based on connections
  const activationLevel = Math.min(connectedCount / 3, 1); // Max at 3 connections

  return (
    <svg
      viewBox="0 0 200 200"
      className={`${className}`}
      style={{ filter: 'drop-shadow(0 4px 12px rgba(59, 130, 246, 0.2))' }}
    >
      <defs>
        {/* Glow filter for connected state */}
        <filter id="glow">
          <feGaussianBlur stdDeviation="3" result="coloredBlur" />
          <feMerge>
            <feMergeNode in="coloredBlur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>

        {/* Radial gradient for nodes */}
        <radialGradient id="nodeGradient">
          <stop offset="0%" stopColor="#60a5fa" />
          <stop offset="100%" stopColor="#3b82f6" />
        </radialGradient>

        {/* Enhanced gradient for activated state */}
        <radialGradient id="nodeGradientActive">
          <stop offset="0%" stopColor="#93c5fd" />
          <stop offset="50%" stopColor="#60a5fa" />
          <stop offset="100%" stopColor="#3b82f6" />
        </radialGradient>
      </defs>

      {/* Synapses (connections between nodes) - render behind nodes */}
      <g className="synapses">
        {SYNAPSES.map((synapse, i) => {
          const fromNode = nodeMap[synapse.from];
          const toNode = nodeMap[synapse.to];

          if (!fromNode || !toNode) return null;

          return (
            <motion.line
              key={`${synapse.from}-${synapse.to}`}
              x1={fromNode.cx}
              y1={fromNode.cy}
              x2={toNode.cx}
              y2={toNode.cy}
              stroke="#60a5fa"
              strokeLinecap="round"
              fill="none"
              initial={{ opacity: 0.3, strokeWidth: 2 }}
              animate={isLearning ? {
                opacity: [0.3, 0.8, 0.3],
                strokeWidth: [2, 3, 2]
              } : { opacity: 0.3, strokeWidth: 2 }}
              transition={isLearning ? {
                duration: 2,
                repeat: Infinity,
                ease: "easeInOut",
                delay: i * 0.2
              } : {}}
            />
          );
        })}
      </g>

      {/* Nodes (brain neurons) */}
      <g className="nodes">
        {BRAIN_NODES.map((node) => (
          <motion.circle
            key={node.id}
            cx={node.cx}
            cy={node.cy}
            r={node.r}
            fill={activationLevel > 0 ? "url(#nodeGradientActive)" : "url(#nodeGradient)"}
            filter="url(#glow)"
            initial={{ scale: 1, opacity: node.opacity }}
            animate={isLearning ? {
              scale: [1, 1.15, 1, 1.08, 1],
              opacity: [0.9, 1, 0.85, 1, 0.9]
            } : { scale: 1, opacity: node.opacity * (0.6 + activationLevel * 0.4) }}
            transition={isLearning ? {
              duration: 3,
              repeat: Infinity,
              ease: "easeInOut"
            } : {}}
          />
        ))}
      </g>

      {/* Core activation ring (appears when connections are made) */}
      {connectedCount > 0 && (
        <motion.circle
          cx={100}
          cy={100}
          r={32}
          fill="none"
          stroke="#60a5fa"
          strokeWidth="2"
          opacity="0"
          animate={{
            r: [32, 40, 32],
            opacity: [0, 0.4, 0],
          }}
          transition={{
            duration: 2,
            repeat: Infinity,
            ease: "easeInOut"
          }}
        />
      )}
    </svg>
  );
};
