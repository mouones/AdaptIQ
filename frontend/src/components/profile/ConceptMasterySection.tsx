/** Shared UI component for ConceptMasterySection behavior. */

import { useEffect, useState } from 'react';
import type { ConceptMasteryItem } from '../../services/customService';

interface ConceptMasterySectionProps {
  concepts: ConceptMasteryItem[];
}

function clampProgress(theta: number): number {
  return Math.max(0, Math.min(100, Math.round(((theta + 3) / 6) * 100)));
}

export default function ConceptMasterySection({ concepts }: ConceptMasterySectionProps) {
  const [isReady, setIsReady] = useState(false);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => setIsReady(true));
    return () => window.cancelAnimationFrame(frame);
  }, []);

  if (concepts.length === 0) {
    return (
      <div className="mb-12 rounded-lg border border-[#2D1B14]/8 bg-white p-8 shadow-sm">
        <div className="mb-3 flex items-center gap-4 text-[#D4AF37]">
          <div className="h-px w-8 bg-current" />
          <h2 className="text-xs font-bold uppercase tracking-[0.3em]">Concept Mastery</h2>
        </div>
        <div className="text-sm text-[#2D1B14]/70">
          No concept data yet. Answer custom room questions to build your mastery map.
        </div>
      </div>
    );
  }

  return (
    <div className="mb-12 rounded-lg border border-[#2D1B14]/8 bg-white p-8 shadow-sm">
      <div className="mb-8 flex items-center gap-4 text-[#D4AF37]">
        <div className="h-px w-8 bg-current" />
        <h2 className="text-xs font-bold uppercase tracking-[0.3em]">Concept Mastery</h2>
      </div>

      <div className="space-y-4">
        {concepts.slice(0, 12).map((concept) => {
          const progress = clampProgress(concept.theta);
          return (
            <div key={concept.concept_id} className="rounded-lg border border-[#2D1B14]/8 p-5 hover:shadow-sm transition-shadow">
              <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                <div>
                  <div className="text-[10px] font-bold uppercase tracking-[0.25em] text-[#2D1B14]/45">
                    {concept.topic}
                  </div>
                  <div className="mt-1 text-lg font-black text-[#2D1B14] font-playfair">
                    {concept.concept}
                  </div>
                  <div className="mt-1 text-xs text-[#2D1B14]/60">
                    Theta {concept.theta.toFixed(2)} • {concept.response_count} responses • {concept.exposure_count} exposures
                  </div>
                </div>

                <div className="rounded-full bg-[#D4AF37]/15 px-4 py-1.5 text-[10px] font-bold uppercase tracking-[0.2em] text-[#2D1B14] border border-[#D4AF37]/20">
                  {concept.mastery_level}
                </div>
              </div>

              <div className="mt-4 h-2 overflow-hidden rounded-full bg-[#2D1B14]/5">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-[#2D1B14] to-[#D4AF37] transition-[width] duration-1000 ease-out"
                  style={{ width: `${isReady ? progress : 0}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}