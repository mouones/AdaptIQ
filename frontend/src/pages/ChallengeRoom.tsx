/**
 * src/pages/ChallengeRoom.tsx
 *
 * Competitive challenge flow with rank-gated levels, streak logic, and promotion states.
 */

import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'motion/react';
import InternalLayout from '../components/InternalLayout';
import { TIMER_SECONDS } from '../config';
import { 
  Trophy, 
  Crown, 
  Lock, 
  ArrowUp, 
  ArrowDown, 
  ArrowLeft, 
  Sparkle, 
  ChevronRight, 
  Flame, 
  BookMarked, 
  ShieldQuestion, 
  Loader2, 
  CheckCircle2, 
  XCircle, 
  ArrowRight,
  Target,
  Zap
} from 'lucide-react';
import { ChallengeLevel, ChallengeQuestion, ChallengeSessionState, Rank, UserRank, TopicType } from '../types/challenge';
import {
  getUserRank,
  startChallengeSession,
  submitChallengeAnswer,
  generateChallengeQuestion,
  endChallengeSession,
  clearChallengePrefetch,
  takePrefetchedQuestion
} from '../services/challengeService';

const RANK_LABELS: Record<Rank, string> = {
  'E': 'Novice',
  'D': 'Apprentice',
  'C': 'Scholar',
  'B': 'Master',
  'A': 'Grandmaster'
};

const LEVEL_BADGES: Record<ChallengeLevel, string> = {
  1: 'E', 2: 'D', 3: 'C', 4: 'B', 5: 'A'
};

type QuestionReview = {
  questionId: string;
  selectedAnswer: string;
  is_correct: boolean;
  correct_answer: string;
  explanation: string;
};

// Render challenge-room flow: level select, question loop, and rank summary.
const ChallengeRoom: React.FC = () => {
  const navigate = useNavigate();
  const [step, setStep] = useState<'selection' | 'quiz' | 'summary'>('selection');
  const [userRank, setUserRank] = useState<UserRank | null>(null);
  const [session, setSession] = useState<ChallengeSessionState | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [selectedAnswer, setSelectedAnswer] = useState<string>('');
  const [isAnswered, setIsAnswered] = useState(false);
  const [forceLevelPopup, setForceLevelPopup] = useState<{ direction: 'up' | 'down', reason: string } | null>(null);
  const [promotionModal, setPromotionModal] = useState<Rank | null>(null);
  const [answerResult, setAnswerResult] = useState<{ is_correct: boolean; correct_answer: string; explanation: string } | null>(null);
  const [questionReview, setQuestionReview] = useState<QuestionReview | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [timeLeft, setTimeLeft] = useState(TIMER_SECONDS);
  const [displayTimeLeft, setDisplayTimeLeft] = useState(TIMER_SECONDS);
  const submitInFlightRef = useRef(false);
  const loadingNextRef = useRef(false);

  useEffect(() => {
    const fetchRank = async () => {
      const rank = await getUserRank();
      setUserRank(rank);
    };
    fetchRank();
  }, []);

  useEffect(() => {
    if (step !== 'quiz' || isLoading || isAnswered || isSubmitting) return;
    if (timeLeft <= 0) {
      const currentQuestion = session?.questions[session.currentIndex];
      if (currentQuestion?.is_free_text) return;
      void handleAnswerSubmit('__timeout__');
      return;
    }
    const timer = window.setInterval(() => setTimeLeft((prev) => prev - 1), 1000);
    return () => window.clearInterval(timer);
  }, [step, isLoading, isAnswered, isSubmitting, timeLeft, session?.currentIndex]);

  useEffect(() => {
    if (isAnswered) return;
    setDisplayTimeLeft(timeLeft);
  }, [timeLeft, isAnswered]);

  // Create a new challenge session at selected starting level.
  const handleStartSession = async (level: ChallengeLevel) => {
    setIsLoading(true);
    try {
      const initialSession = await startChallengeSession('Mixed', level);
      setSession(initialSession);
      setTimeLeft(TIMER_SECONDS);
      setDisplayTimeLeft(TIMER_SECONDS);
      setStep('quiz');
    } catch (error) {
      console.error("Failed to start challenge session:", error);
    } finally {
      setIsLoading(false);
      loadingNextRef.current = false;
    }
  };

  // Submit one challenge answer and update local score/level state.
  const handleAnswerSubmit = async (answer?: string) => {
  if (!session || isAnswered || isSubmitting || submitInFlightRef.current) return;
  
  const answerToSubmit = answer || selectedAnswer;  // use passed answer or state
  if (!answerToSubmit.trim()) return;
  setSelectedAnswer(answerToSubmit);
  setIsSubmitting(true);
  submitInFlightRef.current = true;

  try {
    const result = await submitChallengeAnswer(session, answerToSubmit, TIMER_SECONDS - timeLeft);
    const currentQuestion = session.questions[session.currentIndex] as ChallengeQuestion;
    setAnswerResult({
      is_correct: result.is_correct,
      correct_answer: result.correct_answer,
      explanation: result.explanation,
    });
    setQuestionReview({
      questionId: currentQuestion.id,
      selectedAnswer: answerToSubmit,
      is_correct: result.is_correct,
      correct_answer: result.correct_answer,
      explanation: result.explanation,
    });
    setIsAnswered(true);
    
    // Update session state
    setSession(prev => {
      if (!prev) return null;
      const currentQuestion = prev.questions[prev.currentIndex] as ChallengeQuestion;
      return {
        ...prev,
        score: result.is_correct ? prev.score + 1 : prev.score,
        pointsEarned: prev.pointsEarned + result.points_change,
        rank_points: prev.rank_points + result.points_change,
        streak_correct: result.streak_correct,
        streak_wrong: result.streak_wrong,
        current_level: result.new_level,
        force_level_change: result.force_level_change
      };
    });

    if (result.force_level_change) {
      setForceLevelPopup(result.force_level_change);
      setTimeout(() => setForceLevelPopup(null), 3000);
      clearChallengePrefetch(session.session_id);
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    // Ignore duplicate-submission races gracefully when one request already succeeded.
    if (/(already been answered|duplicate answer|duplicate answer detected)/i.test(message)) {
      setIsAnswered(true);
    } else {
      console.error('Failed to submit challenge answer:', error);
      setSelectedAnswer('');
    }
  } finally {
    setIsSubmitting(false);
    submitInFlightRef.current = false;
    setTimeLeft(TIMER_SECONDS);
    setDisplayTimeLeft(TIMER_SECONDS);
  }
  };

  // Move to next question or finalize challenge run at session end.
  const handleNextQuestion = async () => {
    if (!session || loadingNextRef.current) return;
    loadingNextRef.current = true;
    
    if (session.currentIndex >= 9) {
      // End session on backend first — persists rank points to DB
      try {
        await endChallengeSession(session.session_id);
      } catch (err) {
        console.error('Failed to end challenge session:', err);
      }
      // Check for promotion
      const oldRank = userRank?.current_rank;
      const newRankData = await getUserRank();
      if (oldRank && oldRank !== newRankData.current_rank) {
        setPromotionModal(newRankData.current_rank);
      }
      setUserRank(newRankData);
      setStep('summary');
      loadingNextRef.current = false;
      return;
    }

    setIsLoading(true);
    
    try {
      const expectedLevel = session.current_level;
      let nextQ = takePrefetchedQuestion(session.session_id, expectedLevel);

      if (!nextQ) {
        nextQ = await generateChallengeQuestion(session.topic, expectedLevel, session.session_id);
      }

      // Only unlock/reset the UI after the next question is actually ready.
      // If generation fails after a forced derank, keep the answered review screen
      // visible so the old question cannot be submitted again and cause 409 loops.
      setIsAnswered(false);
      setSelectedAnswer('');
      setAnswerResult(null);
      setQuestionReview(null);
      setIsSubmitting(false);
      submitInFlightRef.current = false;
      setTimeLeft(TIMER_SECONDS);
      setDisplayTimeLeft(TIMER_SECONDS);

      setSession(prev => {
        if (!prev) return null;
        return {
          ...prev,
          currentIndex: prev.currentIndex + 1,
          questions: [...prev.questions, nextQ]
        };
      });
    } catch (error) {
      console.error("Failed to load next question:", error);
      setIsAnswered(true);
      setIsSubmitting(false);
      submitInFlightRef.current = false;
    } finally {
      setIsLoading(false);
      loadingNextRef.current = false;
    }
  };

  if (!userRank) return (
    <InternalLayout>
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-8 h-8 animate-spin text-[#D4AF37]" />
      </div>
    </InternalLayout>
  );

  if (step === 'selection') {
    return (
      <InternalLayout>
        <button onClick={() => navigate('/dashboard')} className="flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[#2D1B14]/60 hover:text-[#D4AF37] mb-12 transition-colors">
          <ArrowLeft className="w-4 h-4" /> Back to Dashboard
        </button>

        <div className="max-w-7xl mx-auto">
          <div className="text-center mb-16">
            <div className="inline-block mb-6">
              <div className="flex items-center gap-3 px-5 py-2 border border-[#D4AF37]/40 rounded-full text-[11px] font-bold uppercase tracking-[0.4em] text-[#D4AF37]">
                <Crown className="w-3 h-3 fill-current" /> Challenge Room
              </div>
            </div>
            <h1 className="text-5xl font-black font-playfair text-[#2D1B14] mb-4">The Gauntlet</h1>
            <p className="text-xl text-[#2D1B14]/60 italic">Ascend through the ranks of the intellectual elite.</p>
          </div>

          {/* Rank Bar */}
          <div className="bg-white p-8 rounded-lg border border-[#2D1B14]/8 shadow-sm mb-12 hover:shadow-md transition-shadow">
            <div className="flex justify-between items-center mb-4">
              <div className="flex items-center gap-4">
                <div className="w-12 h-12 bg-[#2D1B14] text-[#D4AF37] flex items-center justify-center font-black text-2xl border-2 border-[#D4AF37] rounded-md rotate-3">
                  {userRank.current_rank}
                </div>
                <div>
                  <div className="text-xs font-bold uppercase tracking-widest opacity-40">Current Rank</div>
                  <div className="text-xl font-black font-playfair text-[#2D1B14]">{RANK_LABELS[userRank.current_rank]}</div>
                </div>
              </div>
              <div className="text-right">
                <div className="text-xs font-bold uppercase tracking-widest opacity-40">Total Points</div>
                <div className="text-xl font-black font-playfair text-[#2D1B14]">{userRank.total_points}</div>
              </div>
            </div>
            <div className="w-full h-2 bg-[#2D1B14]/5 rounded-full overflow-hidden">
              <motion.div 
                className="h-full bg-[#D4AF37]"
                initial={{ width: 0 }}
                animate={{ width: `${(userRank.total_points % 1000) / 10}%` }}
              />
            </div>
          </div>

          {/* Level Selection */}
          <div className="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-5 gap-6">
            {([1, 2, 3, 4, 5] as ChallengeLevel[]).map((lvl) => {
              const isAvailable = userRank.level_access.includes(lvl);
              return (
                <button
                  key={lvl}
                  disabled={!isAvailable || isLoading}
                  onClick={() => handleStartSession(lvl)}
                  className={`relative group p-8 rounded-lg border transition-all duration-300 flex flex-col items-center text-center ${
                    isAvailable 
                      ? 'bg-white border-[#2D1B14]/8 hover:border-[#D4AF37] hover:shadow-xl cursor-pointer hover:-translate-y-0.5' 
                      : 'bg-[#2D1B14]/5 border-transparent opacity-40 cursor-not-allowed'
                  }`}
                >
                  <div className={`w-12 h-12 mb-6 flex items-center justify-center font-black text-xl border-2 rounded-md ${
                    isAvailable ? 'bg-[#F5F2E7] text-[#2D1B14] border-[#D4AF37]' : 'bg-gray-200 text-gray-400 border-gray-300'
                  }`}>
                    {LEVEL_BADGES[lvl]}
                  </div>
                  <div className="text-sm font-bold uppercase tracking-widest mb-2">Level {lvl}</div>
                  {!isAvailable && <Lock className="w-4 h-4 text-[#2D1B14]/40 mt-auto" />}
                  {isAvailable && <div className="text-[10px] font-bold text-[#D4AF37] mt-auto uppercase tracking-widest group-hover:translate-x-1 transition-transform">Enter →</div>}
                </button>
              );
            })}
          </div>
        </div>
      </InternalLayout>
    );
  }

  if (step === 'quiz' && session) {
    const currentQ = session.questions[session.currentIndex] as ChallengeQuestion;
    const hasReview = questionReview?.questionId === currentQ.id;
    const normalizedCorrectAnswer = (questionReview?.correct_answer || answerResult?.correct_answer || '').trim().toLowerCase();
    
    return (
      <InternalLayout>
        <div className="max-w-7xl mx-auto relative">
          {/* Force Level Popup */}
          <AnimatePresence>
            {forceLevelPopup && (
              <motion.div
                initial={{ opacity: 0, y: -50, scale: 0.8 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, scale: 0.8 }}
                className={`absolute -top-20 left-1/2 -translate-x-1/2 z-50 px-8 py-4 rounded-lg shadow-2xl flex items-center gap-4 border-2 ${
                  forceLevelPopup.direction === 'up' 
                    ? 'bg-green-600 border-green-400 text-white' 
                    : 'bg-red-600 border-red-400 text-white'
                }`}
              >
                {forceLevelPopup.direction === 'up' ? <ArrowUp className="w-6 h-6" /> : <ArrowDown className="w-6 h-6" />}
                <span className="font-bold uppercase tracking-widest text-sm">{forceLevelPopup.reason}</span>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Quiz Header */}
          <div className="flex justify-between items-center mb-12 pb-6 border-b border-[#D4AF37]/20">
            <div className="flex items-center gap-6">
              <div className="w-12 h-12 bg-[#2D1B14] text-[#D4AF37] flex items-center justify-center font-black text-xl border-2 border-[#D4AF37] rounded-md">
                {LEVEL_BADGES[session.current_level]}
              </div>
              <div>
                <div className="text-[10px] font-bold uppercase tracking-[0.3em] text-[#D4AF37]">Question {session.currentIndex + 1} / 10</div>
                <div className="text-sm font-bold text-[#2D1B14]">Level {session.current_level} Challenge</div>
              </div>
            </div>
            
            <div className="flex items-center gap-8">
              <div className="text-center">
                <div className="text-xs font-bold uppercase tracking-widest opacity-40">Timer</div>
                <div className={`text-xl font-black font-mono ${
                  !isAnswered && !isLoading && displayTimeLeft < 10 ? 'text-red-500 animate-pulse' : 'text-[#2D1B14]'
                }`}>
                  {isAnswered ? '—' : isLoading ? '…' : `${displayTimeLeft}s`}
                </div>
              </div>
              <div className="text-center">
                <div className="text-xs font-bold uppercase tracking-widest opacity-40">Rank Points</div>
                <motion.div 
                  key={session.pointsEarned}
                  initial={{ scale: 1.5, color: '#D4AF37' }}
                  animate={{ scale: 1, color: '#2D1B14' }}
                  className="text-2xl font-black font-playfair"
                >
                  {session.pointsEarned}
                </motion.div>
              </div>
              <div className="text-center">
                <div className="text-xs font-bold uppercase tracking-widest opacity-40">Streak</div>
                <div className="flex gap-1 mt-1">
                  {[1, 2, 3, 4].map(i => (
                    <div 
                      key={i} 
                      className={`w-2 h-2 rounded-full transition-all ${
                        i <= session.streak_correct ? 'bg-orange-500 shadow-[0_0_8px_rgba(249,115,22,0.6)]' : 'bg-[#2D1B14]/10'
                      }`} 
                    />
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* Question Area */}
          <div className="mb-12">
            <div className="text-[#D4AF37] text-[10px] font-bold uppercase tracking-[0.4em] mb-4">
              Worth {currentQ.points_value} Points
            </div>
            <h2 className="text-3xl font-black font-playfair text-[#2D1B14] mb-10 leading-relaxed">
              {currentQ.text}
            </h2>

            {currentQ.is_free_text ? (
              <div className="space-y-4">
                <input
                  type="text"
                  value={selectedAnswer}
                  onChange={(e) => setSelectedAnswer(e.target.value)}
                  disabled={isAnswered}
                  placeholder="Type your scholarly answer..."
                  className={`w-full p-6 bg-white rounded-lg border text-xl font-serif italic outline-none transition-all ${
                    hasReview
                      ? questionReview?.is_correct
                        ? 'border-green-500 bg-green-50 text-green-900 shadow-sm'
                        : 'border-red-500 bg-red-50 text-red-900 shadow-sm'
                      : 'border-[#2D1B14]/8 focus:border-[#D4AF37]'
                  }`}
                />
                {!isAnswered && (
                  <button
                    onClick={() => handleAnswerSubmit()}
                    className="w-full py-4 rounded-md bg-[#2D1B14] text-[#F5F2E7] text-[10px] font-bold uppercase tracking-[0.3em] hover:bg-[#3d261c] transition-all shadow-sm"
                  >
                    Submit Answer
                  </button>
                )}
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {currentQ.options.map((option, idx) => {
                  const isSelected = selectedAnswer === option;
                  const normalizedOption = option.trim().toLowerCase();
                  const isCorrect = hasReview && normalizedOption === normalizedCorrectAnswer;
                  const isWrong = hasReview && isSelected && normalizedOption !== normalizedCorrectAnswer;
                  const selectedWasCorrect = hasReview && questionReview?.is_correct;

                  return (
                    <button
                      key={idx}
                      disabled={isAnswered || isSubmitting}
                      onClick={() => {
                        handleAnswerSubmit(option);
                      }}
                      className={`p-6 rounded-lg text-left border transition-all duration-200 flex items-center justify-between group ${
                        isCorrect ? 'bg-green-50 border-green-500 text-green-900 shadow-[0_0_0_1px_rgba(34,197,94,0.15)]' :
                        isWrong ? 'bg-red-50 border-red-500 text-red-900 shadow-[0_0_0_1px_rgba(239,68,68,0.15)]' :
                        isSelected && !hasReview ? 'border-[#D4AF37] bg-[#FDFCF7] shadow-sm' :
                        'bg-white border-[#2D1B14]/8 hover:border-[#D4AF37]/50 hover:shadow-sm'
                      }`}
                    >
                      <span className="font-serif italic">{option}</span>
                      {isCorrect && <CheckCircle2 className="w-5 h-5 text-green-600" />}
                      {isWrong && <XCircle className="w-5 h-5 text-red-600" />}
                      {!isWrong && isSelected && !selectedWasCorrect && !isCorrect && hasReview && (
                        <XCircle className="w-5 h-5 text-red-600" />
                      )}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {/* Explanation */}
          <AnimatePresence>
            {hasReview && (
              <motion.div 
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                className="p-8 bg-white rounded-lg border border-[#2D1B14]/8 shadow-lg"
              >
                {questionReview && (
                  <div className={`flex items-center gap-3 mb-4 ${questionReview.is_correct ? 'text-green-700' : 'text-red-700'}`}>
                    {questionReview.is_correct
                      ? <CheckCircle2 className="w-5 h-5" />
                      : <XCircle className="w-5 h-5" />}
                    <span className="text-xs font-bold uppercase tracking-widest">
                      {questionReview.is_correct ? 'Correct!' : `Wrong \u2014 Answer: ${questionReview.correct_answer}`}
                    </span>
                  </div>
                )}
                <div className="flex items-center gap-3 mb-4">
                  <BookMarked className="w-5 h-5 text-[#D4AF37]" />
                  <span className="text-xs font-bold uppercase tracking-widest text-[#D4AF37]">Scholarly Context</span>
                </div>
                <p className="text-lg font-serif italic text-[#2D1B14]/80 leading-relaxed mb-8">
                  {questionReview?.explanation || answerResult?.explanation || 'Use historical and geographic context to compare closely related options before choosing.'}
                </p>
                <button
                  onClick={handleNextQuestion}
                    disabled={isLoading || isSubmitting}
                  className="w-full py-4 rounded-md bg-[#2D1B14] text-[#F5F2E7] text-[10px] font-bold uppercase tracking-[0.3em] hover:bg-[#3d261c] transition-all flex items-center justify-center gap-2"
                >
                  {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <>Continue Ascent <ArrowRight className="w-4 h-4" /></>}
                </button>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </InternalLayout>
    );
  }

  if (step === 'summary' && session) {
    return (
      <InternalLayout>
        <div className="max-w-6xl mx-auto text-center">
          <motion.div 
            initial={{ scale: 0 }}
            animate={{ scale: 1 }}
            className="w-24 h-24 bg-[#2D1B14] rounded-full flex items-center justify-center border-4 border-[#D4AF37] shadow-2xl mx-auto mb-8"
          >
            <Trophy className="text-[#D4AF37] w-10 h-10" />
          </motion.div>
          
          <h1 className="text-5xl font-black font-playfair text-[#2D1B14] mb-4">Gauntlet Concluded</h1>
          <p className="text-xl text-[#2D1B14]/60 italic mb-12">Your performance has shifted the balance of the archives.</p>

          <div className="grid grid-cols-2 gap-8 mb-16">
            <div className="p-8 bg-white rounded-lg border border-[#2D1B14]/8 hover:shadow-md transition-shadow">
              <div className="text-4xl font-black font-playfair text-[#2D1B14] mb-1">+{session.pointsEarned}</div>
              <div className="text-[10px] font-bold uppercase tracking-widest opacity-40">Rank Points Gained</div>
            </div>
            <div className="p-8 bg-white rounded-lg border border-[#2D1B14]/8 hover:shadow-md transition-shadow">
              <div className="text-4xl font-black font-playfair text-[#2D1B14] mb-1">{userRank.current_rank}</div>
              <div className="text-[10px] font-bold uppercase tracking-widest opacity-40">Current Rank</div>
            </div>
          </div>

          <div className="flex flex-col sm:flex-row gap-4 justify-center">
            <button
              onClick={() => navigate('/dashboard')}
              className="px-10 py-5 rounded-md border border-[#2D1B14] text-[10px] font-bold uppercase tracking-[0.3em] hover:bg-[#2D1B14] hover:text-[#F5F2E7] transition-all"
            >
              Return to Dashboard
            </button>
            <button
              onClick={() => {
                setStep('selection');
                setSession(null);
              }}
              className="px-10 py-5 rounded-md bg-[#2D1B14] text-[#F5F2E7] text-[10px] font-bold uppercase tracking-[0.3em] hover:bg-[#3d261c] transition-all"
            >
              Enter Gauntlet Again
            </button>
          </div>
        </div>

        {/* Promotion Modal */}
        <AnimatePresence>
          {promotionModal && (
            <motion.div 
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 z-[100] bg-[#2D1B14]/90 flex items-center justify-center p-6 backdrop-blur-sm"
            >
              <motion.div 
                initial={{ scale: 0.8, y: 50 }}
                animate={{ scale: 1, y: 0 }}
                className="bg-[#F5F2E7] p-12 max-w-lg w-full text-center border-4 border-[#D4AF37] rounded-xl relative shadow-2xl"
              >
                <div className="absolute -top-12 left-1/2 -translate-x-1/2 w-24 h-24 bg-[#D4AF37] rounded-full flex items-center justify-center shadow-2xl">
                  <Crown className="text-[#2D1B14] w-12 h-12" />
                </div>
                <h2 className="text-4xl font-black font-playfair text-[#2D1B14] mt-8 mb-4">PROMOTED!</h2>
                <p className="text-xl text-[#2D1B14]/60 italic mb-8">You have ascended to Rank {promotionModal}</p>
                <div className="text-6xl font-black text-[#2D1B14] mb-12">{promotionModal}</div>
                <button
                  onClick={() => setPromotionModal(null)}
                  className="w-full py-4 rounded-md bg-[#2D1B14] text-[#F5F2E7] text-[10px] font-bold uppercase tracking-[0.3em] hover:bg-[#3d261c] transition-all"
                >
                  Accept Honor
                </button>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>
      </InternalLayout>
    );
  }

  return null;
};

export default ChallengeRoom;
