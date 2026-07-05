// src/pages/Dashboard.tsx
// Changes from original:
//   1. DashboardWrapper now fetches real onboarding status from backend on mount
//   2. handleOnboardingComplete calls POST /survey
//   3. handleOnboardingSkip calls POST /skip
//   4. handleTourComplete calls POST /mark-tour-seen
//   5. User ID is read from authenticated React state.
//   All visual / layout code is unchanged.
//   Covers dashboard analytics cards, room navigation, and onboarding/tour orchestration.

import React from 'react';
import { useNavigate } from 'react-router-dom';
import InternalLayout from '../components/InternalLayout';
import OnboardingModal from '../components/OnboardingModal';
import GuidedTour from '../components/GuidedTour';
import {
  Trophy, Target, Clock, Zap, ArrowRight, BookOpen,
  Lock, Library, Flame, BarChart3
} from 'lucide-react';
import { DailyTrendPoint, RoomProgress, UserStats } from '../types';
import { fetchDashboardDailyTrend, fetchDashboardStats } from '../services/apiService';
import { useAuth } from '../context/AuthContext';
import {
  getOnboardingStatus,
  submitOnboardingSurvey,
  skipOnboarding,
  markTourSeen,
} from '../services/onboardingService';
import { DASHBOARD_STATS_UPDATED_EVENT } from '../services/dashboardEvents';

// ─── Sub-components (unchanged from original) ────────────────────────────────

// Render a compact stat card used in the dashboard ritual section.
const StatCard: React.FC<{
  title: string; value: string | number; icon: React.ReactNode; subtext?: string
}> = ({ title, value, icon, subtext }) => (
  <div className="bg-white p-7 rounded-lg border border-[#2D1B14]/8 shadow-sm hover:shadow-lg hover:border-l-[#D4AF37] hover:border-l-4 transition-all duration-300 hover:-translate-y-0.5">
    <div className="flex items-center justify-between mb-4">
      <div className="text-[#D4AF37]">{icon}</div>
      <span className="text-[10px] font-bold uppercase tracking-widest opacity-40">{title}</span>
    </div>
    <div className="text-3xl font-black font-playfair text-[#2D1B14]">{value}</div>
    {subtext && <div className="text-xs italic text-[#2D1B14]/60 mt-1">{subtext}</div>}
  </div>
);

// Render one room card with progress, lock state, and navigation action.
const RoomCard: React.FC<{ room: RoomProgress }> = ({ room }) => {
  const navigate = useNavigate();
  return (
    <div id={`room-${room.id}`} className={`relative group ${room.isLocked ? 'opacity-60' : ''}`}>
      <div className="bg-[#FDFCF7] p-8 rounded-lg border border-[#2D1B14]/8 hover:border-[#D4AF37]/60 transition-all duration-500 shadow-sm hover:shadow-xl hover:-translate-y-1 flex flex-col h-full">
        <div className="flex justify-between items-start mb-6">
          <h3 className="text-2xl font-black font-playfair text-[#2D1B14]">{room.name}</h3>
          {room.isLocked ? <Lock className="w-5 h-5 text-[#2D1B14]/30" /> : <BookOpen className="w-5 h-5 text-[#D4AF37]" />}
        </div>
        <p className="text-[#2D1B14]/60 text-sm italic mb-8 flex-grow">{room.description}</p>
        <div className="space-y-4">
          <div className="flex justify-between items-end text-[10px] font-bold uppercase tracking-widest">
            <span>Progress</span>
            <span>{room.progress}%</span>
          </div>
          <div className="w-full h-1.5 bg-[#2D1B14]/5 rounded-full overflow-hidden">
            <div className="h-full bg-[#D4AF37] transition-all duration-1000" style={{ width: `${room.progress}%` }} />
          </div>
          <button
            disabled={room.isLocked}
            onClick={() => navigate(`/rooms/${room.id}`)}
            className={`w-full py-3 rounded-md text-[10px] font-bold uppercase tracking-[0.2em] flex items-center justify-center gap-2 transition-all ${
              room.isLocked
                ? 'bg-[#2D1B14]/5 text-[#2D1B14]/30 cursor-not-allowed'
                : 'bg-[#2D1B14] text-[#F5F2E7] hover:bg-[#3d261c]'
            }`}
          >
            {room.isLocked ? 'Locked' : 'Enter Room'} <ArrowRight className="w-3 h-3" />
          </button>
        </div>
      </div>
    </div>
  );
};

// ─── DashboardContent (unchanged from original) ───────────────────────────────

// Render the main dashboard content once user stats are available.
const DashboardContent: React.FC<{
  stats: UserStats;
  dailyTrend: DailyTrendPoint[];
  statsWarning?: string | null;
  forceRoomsOpen?: boolean;
}> = ({
  stats, dailyTrend, statsWarning, forceRoomsOpen
}) => {
  const weeklyProgress = React.useMemo(() => {
    if (dailyTrend.length > 0) {
      return dailyTrend.slice(-7).map((point) => ({
        day: point.day,
        count: point.count,
      }));
    }
    return [
      { day: 'Mon', count: 0 },
      { day: 'Tue', count: 0 },
      { day: 'Wed', count: 0 },
      { day: 'Thu', count: 0 },
      { day: 'Fri', count: 0 },
      { day: 'Sat', count: 0 },
      { day: 'Sun', count: 0 },
    ];
  }, [dailyTrend]);

  const roomProgress = stats.roomProgress ?? {
    classic: 0,
    challenge: 0,
    pvp: 0,
    custom: 0,
  };

  const roomLocks = stats.roomLocks ?? {
    classic: false,
    challenge: true,
    pvp: true,
    custom: true,
  };

  const rooms: RoomProgress[] = [
    {
      id: 'classic',
      name: 'Classic Room',
      description: 'The core learning environment. Adaptive difficulty and broad knowledge archives.',
      progress: roomProgress.classic,
      isLocked: roomLocks.classic,
    },
    {
      id: 'challenge',
      name: 'Challenge Room',
      description: 'Harder difficulty. Push your analytical boundaries to their absolute limits.',
      progress: roomProgress.challenge,
      isLocked: roomLocks.challenge,
    },
    {
      id: 'pvp',
      name: 'PvP Room',
      description: '1v1 real-time scholarly duels. Test your speed and accuracy against peers.',
      progress: roomProgress.pvp,
      isLocked: roomLocks.pvp,
    },
    {
      id: 'custom',
      name: 'Custom Room',
      description: 'Tailor your inquiry. Select specific historical themes or geographical regions.',
      progress: roomProgress.custom,
      isLocked: roomLocks.custom,
    },
    {
      id: 'visual',
      name: 'Visual Room',
      description: 'Engage with map-based and visual stimuli to test spatial reasoning.',
      progress: 0,
      isLocked: false,
    },
  ];

  const maxWeeklyCount = Math.max(1, ...weeklyProgress.map((d) => d.count));

  return (
    <InternalLayout forceRoomsOpen={forceRoomsOpen}>
      <header className="mb-12 flex justify-between items-end">
        <div>
          <h1 className="text-4xl font-black font-playfair text-[#2D1B14] mb-2">Welcome, Scholar</h1>
          <p className="text-[#2D1B14]/60 italic">Your intellectual legacy continues. The archives await.</p>
        </div>
        <div className="bg-[#2D1B14] p-4 rounded-lg border border-[#D4AF37] rotate-1 hover:rotate-0 transition-all duration-500 shadow-xl hover:shadow-2xl">
          <div className="flex items-center gap-3">
            <Flame className="text-[#D4AF37] w-6 h-6 fill-current animate-pulse" />
            <div>
              <div className="text-[#F5F2E7] text-xl font-black font-playfair">{stats.streakDays ?? 0} Day Streak</div>
              <div className="text-[#D4AF37] text-[8px] font-bold uppercase tracking-widest">Consistency is Mastery</div>
            </div>
          </div>
        </div>
      </header>

      {statsWarning && (
        <div className="mb-8 p-4 bg-amber-50 border border-amber-200 text-amber-700 text-sm">
          Live dashboard stats are temporarily unavailable.
        </div>
      )}

      <section className="mb-16">
        <h2 className="text-xs font-bold uppercase tracking-[0.3em] text-[#D4AF37] mb-6 flex items-center gap-3">
          <Zap className="w-4 h-4 fill-current" /> Today's Rituals
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
          <StatCard title="Questions" value={stats.dailyQuestions}          icon={<BookOpen className="w-5 h-5" />} />
          <StatCard title="Accuracy"  value={`${stats.dailyAccuracy}%`}     icon={<Target   className="w-5 h-5" />} />
          <StatCard title="Time"      value={`${stats.learningTimeMinutes}m`} icon={<Clock    className="w-5 h-5" />} />
          <StatCard title="Points"    value={`+${stats.dailyPoints ?? 0}`}   icon={<Trophy   className="w-5 h-5" />} subtext="Earned today" />
        </div>
      </section>

      <div className="grid lg:grid-cols-3 gap-12 mb-16">
        <section className="lg:col-span-2">
          <h2 className="text-xs font-bold uppercase tracking-[0.3em] text-[#D4AF37] mb-6 flex items-center gap-3">
            <BarChart3 className="w-4 h-4" /> Weekly Illumination
          </h2>
          <div className="bg-white p-8 rounded-lg border border-[#2D1B14]/8 shadow-sm h-64 flex items-end justify-between gap-3">
            {weeklyProgress.map((d, i) => (
              <div key={i} className="flex-1 flex flex-col items-center gap-4">
                <div className="w-full relative group">
                  <div className="w-full bg-[#2D1B14]/5 group-hover:bg-[#D4AF37]/20 transition-colors rounded-t-sm" style={{ height: '140px' }} />
                  <div className="absolute bottom-0 left-0 w-full bg-[#D4AF37] transition-all duration-1000 rounded-t-sm shadow-lg" style={{ height: `${(d.count / maxWeeklyCount) * 140}px` }} />
                  <div className="absolute -top-8 left-1/2 -translate-x-1/2 bg-[#2D1B14] text-[#F5F2E7] text-[8px] px-2 py-1 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-nowrap">
                    {d.count} Questions
                  </div>
                </div>
                <span className="text-[10px] font-bold uppercase tracking-widest opacity-40">{d.day}</span>
              </div>
            ))}
          </div>
        </section>
        <section>
          <h2 className="text-xs font-bold uppercase tracking-[0.3em] text-[#D4AF37] mb-6 flex items-center gap-3">
            <Library className="w-4 h-4" /> Active Rooms
          </h2>
          <div className="space-y-4">
            {rooms.slice(0, 2).map(room => (
              <div key={room.id} className={`p-6 rounded-lg border border-[#2D1B14]/8 bg-white shadow-sm hover:shadow-md transition-shadow ${room.isLocked ? 'opacity-50' : ''}`}>
                <div className="flex justify-between items-center mb-4">
                  <h4 className="font-black font-playfair text-[#2D1B14]">{room.name}</h4>
                  <span className="text-[10px] font-bold text-[#D4AF37]">{room.progress}%</span>
                </div>
                <div className="w-full h-1.5 bg-[#2D1B14]/5 rounded-full overflow-hidden">
                  <div className="h-full bg-[#D4AF37]" style={{ width: `${room.progress}%` }} />
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>

      <section className="mb-16">
        <h2 className="text-xs font-bold uppercase tracking-[0.3em] text-[#D4AF37] mb-6 flex items-center gap-3">
          <Library className="w-4 h-4" /> Learning Chambers
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
          {rooms.map(room => <RoomCard key={room.id} room={room} />)}
        </div>
      </section>

      <section>
        <h2 className="text-xs font-bold uppercase tracking-[0.3em] text-[#D4AF37] mb-6 flex items-center gap-3">
          <Trophy className="w-4 h-4" /> Overall Mastery
        </h2>
        <div className="bg-white p-10 rounded-lg border border-[#2D1B14]/8 shadow-sm flex flex-col md:flex-row justify-between items-center gap-10">
          <div className="flex items-center gap-6">
            <div className="w-20 h-20 rounded-full bg-[#2D1B14] flex items-center justify-center border-4 border-[#D4AF37] shadow-xl">
              <span className="text-2xl font-black font-playfair text-[#D4AF37]">{stats.level[0]}</span>
            </div>
            <div>
              <div className="text-2xl font-black font-playfair text-[#2D1B14]">{stats.level}</div>
              <div className="text-xs font-bold uppercase tracking-widest text-[#D4AF37]">Current Rank</div>
            </div>
          </div>
          <div className="flex gap-16">
            <div className="text-center">
              <div className="text-2xl font-black font-playfair text-[#2D1B14]">{stats.totalQuestions}</div>
              <div className="text-[10px] font-bold uppercase tracking-widest opacity-40">Total Questions</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-black font-playfair text-[#2D1B14]">{stats.globalAccuracy}%</div>
              <div className="text-[10px] font-bold uppercase tracking-widest opacity-40">Global Accuracy</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-black font-playfair text-[#2D1B14]">{stats.points}</div>
              <div className="text-[10px] font-bold uppercase tracking-widest opacity-40">Total Points</div>
            </div>
          </div>
        </div>
      </section>
    </InternalLayout>
  );
};


// ─── DashboardWrapper — THE ONLY PART THAT CHANGED ───────────────────────────

// Orchestrate onboarding state, guided tour visibility, and stats hydration.
const DashboardWrapper: React.FC = () => {
  const { user } = useAuth();
  const [stats, setStats] = React.useState<UserStats>({
    id:                    '',
    points:                0,
    level:                 'Novice',
    totalQuestions:        0,
    globalAccuracy:        0,
    dailyQuestions:        0,
    dailyAccuracy:         0,
    learningTimeMinutes:   0,
    dailyPoints:           0,
    streakDays:            0,
    roomProgress: {
      classic: 0,
      challenge: 0,
      pvp: 0,
      custom: 0,
      visual: 0,
    },
    roomLocks: {
      classic: false,
      challenge: true,
      pvp: true,
      custom: true,
      visual: false,
    },
    firstLogin:            false,
    onboardingCompleted:   false,
    tourSeen:              false,
  });

  const [dailyTrend,      setDailyTrend]     = React.useState<DailyTrendPoint[]>([]);
  const [statsWarning,    setStatsWarning]   = React.useState<string | null>(null);
  const [showOnboarding, setShowOnboarding] = React.useState(false);
  const [showTour,        setShowTour]       = React.useState(false);
  const [userId,          setUserId]         = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;

    const loadDashboardData = async () => {
      try {
        const [statsPayload, trendPayload] = await Promise.all([
          fetchDashboardStats(),
          fetchDashboardDailyTrend(7),
        ]);
        if (cancelled) return;

        setStats(prev => ({ ...prev, ...statsPayload }));
        setDailyTrend(trendPayload.points);
        setStatsWarning(null);
      } catch (err: any) {
        if (cancelled) return;
        setStatsWarning(err?.message ?? 'Failed to load dashboard stats.');
        setDailyTrend([]);
      }
    };

    loadDashboardData();
    return () => {
      cancelled = true;
    };
  }, []);

  React.useEffect(() => {
    let cancelled = false;

    const refreshDashboard = async () => {
      try {
        const [statsPayload, trendPayload] = await Promise.all([
          fetchDashboardStats(),
          fetchDashboardDailyTrend(7),
        ]);
        if (cancelled) return;

        setStats(prev => ({ ...prev, ...statsPayload }));
        setDailyTrend(trendPayload.points);
        setStatsWarning(null);
      } catch {
        if (cancelled) return;
      }
    };

    const handleRefresh = () => {
      void refreshDashboard();
    };

    const handleVisibility = () => {
      if (!document.hidden) {
        void refreshDashboard();
      }
    };

    window.addEventListener(DASHBOARD_STATS_UPDATED_EVENT, handleRefresh);
    window.addEventListener('focus', handleRefresh);
    document.addEventListener('visibilitychange', handleVisibility);

    return () => {
      cancelled = true;
      window.removeEventListener(DASHBOARD_STATS_UPDATED_EVENT, handleRefresh);
      window.removeEventListener('focus', handleRefresh);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, []);

  // Resolve auth user id from React state, then fetch onboarding status.
  React.useEffect(() => {
    const authUserId = user?.id;
    if (!authUserId) return;  // not logged in yet

    setUserId(authUserId);

    getOnboardingStatus(authUserId)
      .then(status => {
        setStats(prev => ({
          ...prev,
          firstLogin:          status.first_login,
          onboardingCompleted: status.onboarding_completed,
          tourSeen:            !status.tour_needed,
        }));

        if (status.onboarding_needed) {
          setShowOnboarding(true);
        } else if (status.tour_needed) {
          setShowTour(true);
        }
      })
      .catch(err => console.error('Onboarding status fetch failed:', err));
  }, [user?.id]);

  // ── Complete survey → POST /survey ─────────────────────────────────────────
  // Persist onboarding survey answers and proceed to guided tour.
  const handleOnboardingComplete = async (confident: string[], learn: string[]) => {
    if (userId) {
      try {
        await submitOnboardingSurvey(userId, confident, learn);
      } catch (err) {
        console.error('Survey submit failed:', err);
      }
    }
    setStats(prev => ({ ...prev, firstLogin: false, onboardingCompleted: true, tourSeen: false }));
    setShowOnboarding(false);
    setShowTour(true);   // tour follows onboarding
  };

  // ── Skip survey → POST /skip ───────────────────────────────────────────────
  // Mark onboarding skipped while still launching tour flow.
  const handleOnboardingSkip = async () => {
    if (userId) {
      try {
        await skipOnboarding(userId);
      } catch (err) {
        console.error('Skip onboarding failed:', err);
      }
    }
    setStats(prev => ({ ...prev, firstLogin: false, onboardingCompleted: true, tourSeen: false }));
    setShowOnboarding(false);
    setShowTour(true);
  };

  // ── Tour done / skipped → POST /mark-tour-seen ────────────────────────────
  // Persist tour completion and close the tour overlay.
  const handleTourComplete = async () => {
    if (userId) {
      try {
        await markTourSeen(userId);
      } catch (err) {
        console.error('Mark tour seen failed:', err);
      }
    }
    setStats(prev => ({ ...prev, tourSeen: true }));
    setShowTour(false);
  };

  return (
    <>
      <DashboardContent
        stats={stats}
        dailyTrend={dailyTrend}
        statsWarning={statsWarning}
        forceRoomsOpen={showTour}
      />
      {showOnboarding && (
        <OnboardingModal
          onComplete={handleOnboardingComplete}
          onSkip={handleOnboardingSkip}
        />
      )}
      {showTour && (
        <GuidedTour onComplete={handleTourComplete} />
      )}
    </>
  );
};

export default DashboardWrapper;
