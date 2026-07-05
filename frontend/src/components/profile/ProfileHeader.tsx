/** Shared UI component for ProfileHeader behavior. */

import RankBadge from './RankBadge';

interface ProfileHeaderProps {
  username: string;
  email: string;
  level: string;
  points: number;
  memberSince: string;
  profilePicture?: string;
}

export default function ProfileHeader({ username, email, level, points, memberSince, profilePicture }: ProfileHeaderProps) {
  return (
    <div className="relative overflow-hidden rounded-xl bg-gradient-to-br from-[#2a1a14] via-[#452a1f] to-[#2a1a14] p-8 shadow-2xl md:p-12">
      <div className="absolute inset-0 opacity-10 pointer-events-none">
        <div
          className="absolute inset-0"
          style={{
            backgroundImage: 'radial-gradient(circle at 2px 2px, #D4AF37 1px, transparent 0)',
            backgroundSize: '40px 40px',
          }}
        />
      </div>

      <div className="relative z-10 flex flex-col gap-8 md:flex-row md:items-end md:justify-between">
        <div className="flex flex-col items-center gap-5 text-center md:flex-row md:items-end md:text-left">
          <div className="flex h-24 w-24 items-center justify-center overflow-hidden rounded-full border-4 border-[#D4AF37]/50 bg-[#D4AF37] shadow-[0_0_20px_rgba(212,175,55,0.3)] ring-4 ring-[#D4AF37]/20 md:h-28 md:w-28">
            <img
              src={profilePicture || `https://api.dicebear.com/7.x/bottts/svg?seed=${encodeURIComponent(username)}`}
              alt={username}
              className="h-full w-full object-cover"
            />
          </div>

          <div>
            <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.35em] text-[#F5F2E7]/50">
              Scholar Archive
            </div>
            <h1 className="mb-3 text-4xl font-black tracking-tight text-[#F5F2E7] md:text-5xl font-playfair">
              {username}
            </h1>
            <div className="max-w-xl text-sm text-[#F5F2E7]/70">
              {email}
            </div>
            <div className="mt-3 text-[10px] font-bold uppercase tracking-[0.3em] text-[#F5F2E7]/55">
              {memberSince}
            </div>
          </div>
        </div>

        <RankBadge level={level} points={points} />
      </div>
    </div>
  );
}