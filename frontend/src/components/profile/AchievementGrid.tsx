/** Shared UI component for AchievementGrid behavior. */

import { BookOpen, Flame, ShieldCheck, Sparkles, Target, Trophy } from 'lucide-react';

interface AchievementItem {
  key: string;
  name: string;
  description: string;
  icon: string;
  locked: boolean;
  unlocked_at?: string | null;
}

interface AchievementGridProps {
  achievements: AchievementItem[];
}

function iconFor(name: string) {
  const normalized = name.toLowerCase();
  if (normalized.includes('streak') || normalized.includes('daily')) return Flame;
  if (normalized.includes('master') || normalized.includes('knowledge')) return BookOpen;
  if (normalized.includes('accuracy') || normalized.includes('focus')) return Target;
  if (normalized.includes('leader') || normalized.includes('champion')) return Trophy;
  if (normalized.includes('archive') || normalized.includes('profile')) return ShieldCheck;
  return Sparkles;
}

export default function AchievementGrid({ achievements }: AchievementGridProps) {
  if (achievements.length === 0) {
    return null;
  }

  return (
    <div className="mb-12">
      <div className="mb-8 flex items-center gap-4 text-[#D4AF37]">
        <div className="h-px w-8 bg-current" />
        <h2 className="text-xs font-bold uppercase tracking-[0.3em]">Achievements</h2>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {achievements.map((achievement) => {
          const Icon = iconFor(achievement.name);
          return (
            <div
              key={achievement.key}
              className={`rounded-sm border p-5 shadow-sm transition-all ${achievement.locked ? 'border-[#2D1B14]/10 bg-white/80' : 'border-[#D4AF37]/25 bg-[#D4AF37]/5'}`}
            >
              <div className="flex items-start gap-4">
                <div className={`flex h-12 w-12 items-center justify-center rounded-full ${achievement.locked ? 'bg-[#2D1B14]/10 text-[#2D1B14]/45' : 'bg-[#D4AF37] text-[#2D1B14]'}`}>
                  <Icon className="h-5 w-5" />
                </div>

                <div className="min-w-0 flex-1">
                  <div className="text-lg font-black text-[#2D1B14] font-playfair">
                    {achievement.name}
                  </div>
                  <div className="mt-1 text-sm text-[#2D1B14]/65">
                    {achievement.description}
                  </div>
                  <div className="mt-3 text-[10px] font-bold uppercase tracking-[0.2em] text-[#2D1B14]/45">
                    {achievement.locked ? 'Locked' : 'Unlocked'}
                    {achievement.unlocked_at ? ` • ${new Date(achievement.unlocked_at).toLocaleDateString()}` : ''}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}