/** Shared UI component for LearningStreakSection behavior. */

import { Flame, Trophy, Sparkles, Clock3 } from 'lucide-react';

interface LearningStreakSectionProps {
  streakDays: number;
  dailyQuestions: number;
  dailyAccuracy: number;
  dailyPoints: number;
  learningTimeMinutes: number;
}

export default function LearningStreakSection({
  streakDays,
  dailyQuestions,
  dailyAccuracy,
  dailyPoints,
  learningTimeMinutes,
}: LearningStreakSectionProps) {
  return (
    <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
      <div className="rounded-lg border bg-white p-6 shadow-sm border-[#2D1B14]/8 hover:shadow-md transition-shadow">
        <div className="flex items-center gap-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[#2D1B14] text-white">
            <Flame className="h-6 w-6" />
          </div>
          <div>
            <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.2em] opacity-60">
              Learning Streak
            </div>
            <div className="text-2xl font-black text-[#2D1B14] font-playfair">
              {streakDays} day{streakDays === 1 ? '' : 's'}
            </div>
            <div className="mt-1 text-xs italic opacity-60">
              {streakDays === 0 ? 'Answer a question today to start your run.' : 'You have activity on consecutive days.'}
            </div>
          </div>
        </div>
      </div>

      <div className="rounded-lg border bg-[#D4AF37]/5 p-6 border-[#D4AF37]/20 hover:shadow-md transition-shadow">
        <div className="flex items-center gap-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[#D4AF37] text-white">
            <Trophy className="h-6 w-6" />
          </div>
          <div>
            <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.2em] opacity-60">
              Today's Focus
            </div>
            <div className="text-2xl font-black text-[#2D1B14] font-playfair">
              {dailyPoints} pts earned
            </div>
            <div className="mt-1 text-xs italic opacity-60">
              {dailyQuestions} question{dailyQuestions === 1 ? '' : 's'} • {dailyAccuracy.toFixed(1)}% accuracy • {learningTimeMinutes} min learning
            </div>
          </div>
        </div>
      </div>

      <div className="rounded-lg border bg-white p-6 shadow-sm border-[#2D1B14]/8 hover:shadow-md transition-shadow">
        <div className="flex items-center gap-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[#2D1B14] text-white">
            <Sparkles className="h-6 w-6" />
          </div>
          <div>
            <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.2em] opacity-60">
              Focus Snapshot
            </div>
            <div className="text-2xl font-black text-[#2D1B14] font-playfair">
              {dailyAccuracy.toFixed(1)}% accuracy
            </div>
            <div className="mt-1 text-xs italic opacity-60">
              Your current daily performance across all rooms.
            </div>
          </div>
        </div>
      </div>

      <div className="rounded-lg border bg-white p-6 shadow-sm border-[#2D1B14]/8 hover:shadow-md transition-shadow">
        <div className="flex items-center gap-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[#2D1B14] text-white">
            <Clock3 className="h-6 w-6" />
          </div>
          <div>
            <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.2em] opacity-60">
              Learning Time
            </div>
            <div className="text-2xl font-black text-[#2D1B14] font-playfair">
              {learningTimeMinutes} min
            </div>
            <div className="mt-1 text-xs italic opacity-60">
              Time spent answering questions today.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}