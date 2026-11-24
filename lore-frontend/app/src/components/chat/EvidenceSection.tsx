import { useState } from "react";
import { ChevronRight, FileSearch } from "lucide-react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { type CitationDetail } from "@/services/loreApi";
import { EvidenceCard } from "./EvidenceCard";

interface EvidenceSectionProps {
  citations: CitationDetail[];
}

export function EvidenceSection({ citations }: EvidenceSectionProps) {
  const [open, setOpen] = useState(false);

  if (citations.length === 0) return null;

  return (
    <Collapsible open={open} onOpenChange={setOpen} className="mt-3">
      <CollapsibleTrigger className="flex items-center gap-2 px-3 py-2 rounded-lg bg-slate-50 hover:bg-slate-100 transition-colors text-sm text-slate-500 hover:text-slate-700 w-full">
        <FileSearch className="w-4 h-4" />
        <span className="font-medium">
          Evidence &mdash; {citations.length} source{citations.length !== 1 ? "s" : ""}
        </span>
        <ChevronRight
          className={`w-4 h-4 ml-auto transition-transform duration-200 ${
            open ? "rotate-90" : ""
          }`}
        />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mt-2 space-y-2">
          {citations.map((citation) => (
            <EvidenceCard key={citation.chunk_id} citation={citation} />
          ))}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}
