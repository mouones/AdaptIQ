// Shared frontend types for Classic Room and dashboard payloads.

// Must match the backend topic labels used by Classic, Challenge, and stats APIs.
export type TopicType = 'Geography' | 'History' | 'Mixed';

export interface Question {
  id: string;
  text: string;
  options: string[];
  correctAnswer: string;
  explanation: string;
  image?: string;
  topic?: TopicType;
  difficulty?: number;
}

export interface UserStats {
  id: string;
  points: number;
  level: string;
  totalQuestions: number;
  globalAccuracy: number;
  dailyQuestions: number;
  dailyAccuracy: number;
  learningTimeMinutes: number;
  dailyPoints?: number;
  streakDays?: number;
  roomProgress?: {
    classic: number;
    challenge: number;
    pvp: number;
    custom: number;
    visual: number;
  };
  roomLocks?: {
    classic: boolean;
    challenge: boolean;
    pvp: boolean;
    custom: boolean;
    visual: boolean;
  };
  firstLogin?: boolean;
  onboardingCompleted?: boolean;
  tourSeen?: boolean;
}

export interface DailyTrendPoint {
  date: string;
  day: string;
  count: number;
  correct: number;
  points: number;
}

export interface RoomProgress {
  id: string;
  name: string;
  description: string;
  progress: number;
  isLocked: boolean;
}

export interface QuizSessionState {
  topic: TopicType;
  questions: Question[];
  currentIndex: number;
  score: number;
  pointsEarned: number;
  hintsUsed: number;
  startTime: number;
  isFinished: boolean;
}
