import { type CitationDetail } from "@/services/loreApi";
import {
  FileText,
  Lightbulb,
  GitBranch,
  UserCheck,
  Link2,
  AlertTriangle,
  Info,
  Hash,
  Users,
  Calendar,
  Shield,
} from "lucide-react";

const TYPE_CONFIG: Record<
  string,
  { label: string; icon: React.ElementType; color: string }
> = {
  decision: {
    label: "Decision",
    icon: GitBranch,
    color: "bg-purple-100 text-purple-700 border-purple-200",
  },
  fact: {
    label: "Fact",
    icon: FileText,
    color: "bg-blue-100 text-blue-700 border-blue-200",
  },
  process: {
    label: "Process",
    icon: Lightbulb,
    color: "bg-green-100 text-green-700 border-green-200",
  },
  expertise_signal: {
    label: "Expertise",
    icon: UserCheck,
    color: "bg-amber-100 text-amber-700 border-amber-200",
  },
  relationship: {
    label: "Relationship",
    icon: Link2,
    color: "bg-cyan-100 text-cyan-700 border-cyan-200",
  },
  incident: {
    label: "Incident",
    icon: AlertTriangle,
    color: "bg-red-100 text-red-700 border-red-200",
  },
  context: {
    label: "Context",
    icon: Info,
    color: "bg-slate-100 text-slate-700 border-slate-200",
  },
};

function ConfidenceIndicator({ confidence }: { confidence: number }) {
  const percentage = Math.round(confidence * 100);
  const colorClass =
    confidence >= 0.8
      ? "text-green-600"
      : confidence >= 0.6
        ? "text-amber-600"
        : "text-red-500";

  return (
    <span className={`inline-flex items-center gap-1 text-xs ${colorClass}`}>
      <Shield className="w-3 h-3" />
      {percentage}%
    </span>
  );
}

interface EvidenceCardProps {
  citation: CitationDetail;
}

export function EvidenceCard({ citation }: EvidenceCardProps) {
  const config = TYPE_CONFIG[citation.type] || TYPE_CONFIG.context;
  const Icon = config.icon;

  const formattedDate = citation.knowledge_date
    ? new Date(citation.knowledge_date).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
      })
    : null;

  return (
    <div className="bg-white border border-slate-200 rounded-xl p-4 hover:border-blue-200 hover:shadow-sm transition-all">
      {/* Header: type badge + source + confidence */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span
            className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium border ${config.color}`}
          >
            <Icon className="w-3 h-3" />
            {config.label}
          </span>
          {citation.source_platform && (
            <span className="text-xs text-slate-400 capitalize">
              {citation.source_platform}
              {citation.source_channel ? ` / ${citation.source_channel}` : ""}
            </span>
          )}
        </div>
        <ConfidenceIndicator confidence={citation.confidence} />
      </div>

      {/* Content */}
      <p className="text-sm text-slate-700 leading-relaxed mb-3">
        {citation.content}
      </p>

      {/* Metadata footer */}
      <div className="flex flex-wrap items-center gap-3 text-xs text-slate-400">
        {citation.people_involved.length > 0 && (
          <span className="inline-flex items-center gap-1">
            <Users className="w-3 h-3" />
            {citation.people_involved.join(", ")}
          </span>
        )}
        {citation.topics.length > 0 && (
          <span className="inline-flex items-center gap-1">
            <Hash className="w-3 h-3" />
            {citation.topics.join(", ")}
          </span>
        )}
        {formattedDate && (
          <span className="inline-flex items-center gap-1">
            <Calendar className="w-3 h-3" />
            {formattedDate}
          </span>
        )}
      </div>
    </div>
  );
}
