/** Shared UI component for RankBadge behavior. */

import { Award } from 'lucide-react';

interface RankBadgeProps {
  level: string;
  points: number;
}

export default function RankBadge({ level, points }: RankBadgeProps) {
  return (
    <div className="inline-flex items-center gap-3 rounded-full border border-white/15 bg-white/10 px-4 py-2 backdrop-blur-sm">
      <div className="rounded-full bg-[#D4AF37] p-1.5">
        <Award className="h-5 w-5 text-[#2D1B14]" />
      </div>
      <div className="text-left">
        <div className="text-[10px] font-bold uppercase tracking-[0.3em] text-[#F5F2E7]/60 leading-none">Rank</div>
        <div className="text-sm font-black text-[#F5F2E7]">
          {level} • {points.toLocaleString()} pts
        </div>
      </div>
    </div>
  );
}