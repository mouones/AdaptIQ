/** Render the VisualRoomQuiz page flow. */

import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import InternalLayout from '../components/InternalLayout';
import {
  ArrowLeft,
  BookOpen,
  CheckCircle2,
  Flame,
  Loader2,
  ShieldQuestion,
  Sparkle,
  Target,
  Trophy,
  XCircle,
  Lightbulb,
  ChevronRight,
} from 'lucide-react';
import type { TopicType } from '../types';
import type { VisualQuestion, VisualEndSessionResponse } from '../types/visual';
import {
  startVisualSession,
  fetchNextVisualQuestion,
  submitVisualAnswer,
  fetchVisualHint,
  endVisualSession,
} from '../services/visualRoomService';

const TOTAL_QUESTIONS = 10;
const TIMER_SECONDS = 30;

type Step = 'selection' | 'quiz' | 'summary';

const VisualRoomQuiz: React.FC = () => {
  const navigate = useNavigate();

  const [step, setStep] = useState<Step>('selection');
  const [topic, setTopic] = useState<TopicType>('Mixed');
  const [level, setLevel] = useState<number>(1);

  const [sessionId, setSessionId] = useState<string | null>(null);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [score, setScore] = useState(0);
  const [pointsEarned, setPointsEarned] = useState(0);
  const [hintsUsed, setHintsUsed] = useState(0);
  const [startTime, setStartTime] = useState<number | null>(null);
  const [streakCorrect, setStreakCorrect] = useState(0);
  const [streakWrong, setStreakWrong] = useState(0);

  const [isLoading, setIsLoading] = useState(false);
  const [question, setQuestion] = useState<VisualQuestion | null>(null);
  const [selectedAnswer, setSelectedAnswer] = useState<string | null>(null);
  const [textAnswer, setTextAnswer] = useState('');
  const [isAnswered, setIsAnswered] = useState(false);
  const [feedback, setFeedback] = useState<{ is_correct: boolean; correct_answer: string; explanation: string; next_question: VisualQuestion | null } | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const [isHintLoading, setIsHintLoading] = useState(false);
  const [timeLeft, setTimeLeft] = useState(TIMER_SECONDS);
  const [endSummary, setEndSummary] = useState<VisualEndSessionResponse | null>(null);

  const isTextMode = useMemo(() => {
    if (!question) return false;
    return question.question_type === 'T' || question.options.length === 0;
  }, [question]);

  // ── Timer ───────────────────────────────────────────────────────────────
  useEffect(() => {
    if (step !== 'quiz' || isLoading || isAnswered) return;
    if (timeLeft <= 0) {
      // Auto-submit wrong answer on timeout
      void handleSubmit('');
      return;
    }
    const timer = setInterval(() => setTimeLeft(t => t - 1), 1000);
    return () => clearInterval(timer);
  }, [step, isLoading, isAnswered, timeLeft]);

  const resetPerQuestionState = () => {
    setSelectedAnswer(null);
    setTextAnswer('');
    setIsAnswered(false);
    setFeedback(null);
    setHint(null);
    setTimeLeft(TIMER_SECONDS);
  };

  const loadQuestion = async (sid: string, prefetched?: VisualQuestion | null) => {
    setIsLoading(true);
    resetPerQuestionState();
    try {
      const q = prefetched ?? await fetchNextVisualQuestion(sid);
      setQuestion(q);
    } catch (err) {
      console.error('Failed to fetch visual question', err);
      setQuestion(null);
    } finally {
      setIsLoading(false);
    }
  };

  const handleStart = async () => {
    setIsLoading(true);
    try {
      const s = await startVisualSession(topic, level);
      setSessionId(s.session_id);
      setCurrentIndex(0);
      setScore(0);
      setPointsEarned(0);
      setHintsUsed(0);
      setStartTime(Date.now());
      setStreakCorrect(0);
      setStreakWrong(0);
      setEndSummary(null);
      setStep('quiz');
      await loadQuestion(s.session_id);
    } catch (err) {
      console.error('Failed to start visual session', err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleSubmit = async (answer: string) => {
    if (!sessionId || !question || isAnswered) return;

    setIsAnswered(true);
    setSelectedAnswer(answer);

    const timeTakenSeconds = TIMER_SECONDS - timeLeft;
    try {
      const res = await submitVisualAnswer(
        sessionId,
        question.id,
        answer,
        timeTakenSeconds * 1000,
      );
      setFeedback(res);
      setLevel(Math.max(1, Math.min(5, res.current_level || res.next_question?.level || level)));

      const speedBonus = Math.floor(timeLeft / 3);
      if (res.is_correct) {
        setScore(prev => prev + 1);
        setPointsEarned(prev => prev + 10 + speedBonus);
        setStreakCorrect(prev => (prev + 1 >= 4 ? 0 : prev + 1));
        setStreakWrong(0);
      } else {
        setStreakCorrect(0);
        setStreakWrong(prev => (prev + 1 >= 2 ? 0 : prev + 1));
      }
    } catch (err) {
      console.error('Submit failed', err);
      // Allow user to proceed even on failure
      setFeedback({
        is_correct: false,
        correct_answer: 'Unknown',
        explanation: 'Could not verify your answer due to a network/server error.',
        next_question: null,
      });
    }
  };

  const handleHint = async () => {
    if (!question || hint || isHintLoading || isAnswered) return;
    setIsHintLoading(true);
    try {
      if (!sessionId) return;
      const hintText = await fetchVisualHint(question.id, sessionId);
      setHint(hintText);
      setHintsUsed(prev => prev + 1);
      setPointsEarned(prev => Math.max(0, prev - 3));
    } catch (err) {
      console.error('Failed to fetch hint', err);
    } finally {
      setIsHintLoading(false);
    }
  };

  const handleNext = async () => {
    if (!sessionId) return;

    const nextIdx = currentIndex + 1;
    if (nextIdx >= TOTAL_QUESTIONS) {
      try {
        const summary = await endVisualSession(sessionId);
        setEndSummary(summary);
      } catch (err) {
        console.warn('endVisualSession failed (non-fatal):', err);
      }
      setStep('summary');
      return;
    }

    setCurrentIndex(nextIdx);
    await loadQuestion(sessionId, feedback?.next_question ?? null);
  };

  const timeSpentMin = useMemo(() => {
    if (!startTime) return 0;
    return Math.round((Date.now() - startTime) / 1000 / 60);
  }, [startTime, step]);

  // ── Selection ────────────────────────────────────────────────────────────
  if (step === 'selection') {
    return (
      <InternalLayout>
        <button onClick={() => navigate('/dashboard')} className="flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[#2D1B14]/60 hover:text-[#D4AF37] mb-12 transition-colors">
          <ArrowLeft className="w-4 h-4" /> Back to Dashboard
        </button>

        <div className="max-w-6xl mx-auto text-center">
          <div className="inline-block mb-8">
            <div className="flex items-center gap-3 px-5 py-2 border border-[#D4AF37]/40 rounded-full text-[11px] font-bold uppercase tracking-[0.4em] text-[#D4AF37]">
              <Sparkle className="w-3 h-3 fill-current" /> Visual Room
            </div>
          </div>

          <h1 className="text-5xl font-black font-playfair text-[#2D1B14] mb-6">Read the Image</h1>
          <p className="text-xl text-[#2D1B14]/60 italic mb-12">Choose a topic and depth of inquiry.</p>

          <div className="grid grid-cols-1 gap-6 mb-10">
            {(
              [
                { id: 'History', icon: <BookOpen className="w-6 h-6" />, desc: 'Interpret imagery through historical context.' },
                { id: 'Geography', icon: <ShieldQuestion className="w-6 h-6" />, desc: 'Reason about places, landscapes, and maps.' },
                { id: 'Mixed', icon: <Flame className="w-6 h-6" />, desc: 'A blend of visual knowledge across domains.' },
              ] as const
            ).map((t) => (
              <button
                key={t.id}
                onClick={() => setTopic(t.id)}
                disabled={isLoading}
                className={`group relative bg-white p-8 rounded-lg border transition-all duration-300 text-left flex items-center gap-8 shadow-sm hover:shadow-xl hover:-translate-y-0.5 ${
                  topic === t.id ? 'border-[#D4AF37]' : 'border-[#2D1B14]/8 hover:border-[#D4AF37]'
                }`}
              >
                <div className={`w-16 h-16 bg-[#F5F2E7] rounded-md flex items-center justify-center transition-all ${
                  topic === t.id ? 'text-[#D4AF37] bg-[#2D1B14]' : 'text-[#2D1B14] group-hover:bg-[#2D1B14] group-hover:text-[#D4AF37]'
                }`}>
                  {t.icon}
                </div>
                <div>
                  <div className="text-xl font-black font-playfair text-[#2D1B14]">{t.id}</div>
                  <div className="text-sm italic text-[#2D1B14]/60">{t.desc}</div>
                </div>
                <ChevronRight className="ml-auto w-6 h-6 text-[#D4AF37] opacity-0 group-hover:opacity-100 transition-all" />
              </button>
            ))}
          </div>

          <div className="bg-white p-8 rounded-lg border border-[#2D1B14]/8 shadow-sm mb-10 text-left">
            <div className="text-[10px] font-bold uppercase tracking-widest opacity-40 mb-4">Level</div>
            <div className="grid grid-cols-5 gap-3">
              {[1, 2, 3, 4, 5].map((lvl) => (
                <button
                  key={lvl}
                  onClick={() => setLevel(lvl)}
                  disabled={isLoading}
                  className={`py-3 rounded-md border text-[10px] font-bold uppercase tracking-widest transition-all ${
                    level === lvl
                      ? 'bg-[#2D1B14] text-[#D4AF37] border-[#D4AF37]'
                      : 'bg-white text-[#2D1B14] border-[#2D1B14]/8 hover:border-[#D4AF37]'
                  }`}
                >
                  {lvl}
                </button>
              ))}
            </div>
          </div>

          <button
            onClick={handleStart}
            disabled={isLoading}
            className="w-full py-4 rounded-md bg-[#2D1B14] text-[#F5F2E7] text-[10px] font-bold uppercase tracking-[0.2em] hover:bg-[#3d261c] transition-colors flex items-center justify-center gap-3 shadow-sm"
          >
            {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkle className="w-4 h-4" />}
            Begin Session
          </button>
        </div>
      </InternalLayout>
    );
  }

  // ── Summary ──────────────────────────────────────────────────────────────
  if (step === 'summary') {
    const accuracy = endSummary
      ? Math.round(endSummary.accuracy_percent)
      : Math.round((score / TOTAL_QUESTIONS) * 100);

    return (
      <InternalLayout>
        <div className="max-w-7xl mx-auto py-10">
          <header className="mb-12 text-center">
            <div className="w-20 h-20 bg-[#D4AF37] rounded-full flex items-center justify-center mx-auto mb-6 shadow-xl shadow-yellow-200">
              <Trophy className="w-10 h-10 text-[#2D1B14]" />
            </div>
            <h1 className="text-4xl font-black font-playfair text-[#2D1B14] mb-2 tracking-tight">Inquiry Concluded</h1>
            <p className="text-[#2D1B14]/60 italic font-serif leading-relaxed">Your visual interpretation has been chronicled.</p>
          </header>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-12">
            <div className="bg-white p-8 rounded-lg border border-[#2D1B14]/8 shadow-sm text-center hover:shadow-md transition-shadow">
              <Target className="w-6 h-6 text-[#D4AF37] mx-auto mb-4" />
              <div className="text-3xl font-black font-playfair text-[#2D1B14]">{accuracy}%</div>
              <div className="text-[10px] font-bold uppercase tracking-widest opacity-40">Accuracy</div>
            </div>
            <div className="bg-white p-8 rounded-lg border border-[#2D1B14]/8 shadow-sm text-center hover:shadow-md transition-shadow">
              <div className="text-3xl font-black font-playfair text-[#2D1B14]">{score} / {TOTAL_QUESTIONS}</div>
              <div className="text-[10px] font-bold uppercase tracking-widest opacity-40">Correct Interpretations</div>
            </div>
            <div className="bg-white p-8 rounded-lg border border-[#2D1B14]/8 shadow-sm text-center hover:shadow-md transition-shadow">
              <div className="text-3xl font-black font-playfair text-[#2D1B14]">{timeSpentMin}m</div>
              <div className="text-[10px] font-bold uppercase tracking-widest opacity-40">Time Invested</div>
            </div>
          </div>

          <div className="bg-[#2D1B14] rounded-lg p-10 text-[#F5F2E7] mb-12 shadow-xl">
            <h3 className="text-xl font-black font-playfair text-[#D4AF37] mb-4">Session Notes</h3>
            <p className="text-sm font-serif italic text-white/70 mb-8 leading-relaxed">
              Hints used: {hintsUsed}. Points earned: {pointsEarned}.
            </p>
            <div className="flex flex-wrap gap-4">
              <button
                onClick={() => {
                  setStep('selection');
                  setSessionId(null);
                  setQuestion(null);
                }}
                className="px-8 py-4 rounded-md bg-[#D4AF37] text-[#2D1B14] font-bold uppercase tracking-widest text-xs hover:bg-[#e6c75a] transition-colors"
              >
                Run Again
              </button>
              <button
                onClick={() => navigate('/dashboard')}
                className="px-8 py-4 rounded-md border border-white/20 text-white font-bold uppercase tracking-widest text-xs hover:bg-white/10 transition-colors"
              >
                Dashboard
              </button>
            </div>
          </div>
        </div>
      </InternalLayout>
    );
  }

  // ── Quiz ─────────────────────────────────────────────────────────────────
  return (
    <InternalLayout>
      <div className="max-w-7xl mx-auto">
        <div className="flex justify-between items-center mb-10 pb-6 border-b border-[#D4AF37]/20">
          <div>
            <div className="text-[10px] font-bold uppercase tracking-[0.3em] text-[#D4AF37]">Visual Room • Level {level}</div>
            <div className="text-sm font-bold text-[#2D1B14]">Question {currentIndex + 1} / {TOTAL_QUESTIONS}</div>
            <div className="text-xs text-[#2D1B14]/50 italic">Streak: {streakCorrect} correct / {streakWrong} wrong</div>
          </div>
          <div className="flex items-center gap-8">
            <div className="text-center">
              <div className="text-xs font-bold uppercase tracking-widest opacity-40">Timer</div>
              <div className={`text-xl font-black font-mono ${timeLeft < 10 ? 'text-red-500 animate-pulse' : 'text-[#2D1B14]'}`}>
                {timeLeft}s
              </div>
            </div>
            <div className="text-center">
              <div className="text-xs font-bold uppercase tracking-widest opacity-40">Points</div>
              <div className="text-xl font-black font-playfair text-[#2D1B14]">{pointsEarned}</div>
            </div>
          </div>
        </div>

        {isLoading && (
          <div className="flex items-center justify-center gap-3 text-[#D4AF37] font-bold uppercase tracking-widest text-xs mb-10">
            <Loader2 className="w-5 h-5 animate-spin" /> Preparing the next prompt...
          </div>
        )}

        {!question && !isLoading && (
          <div className="bg-white p-8 border border-[#2D1B14]/10 shadow-sm">
            <h2 className="text-2xl font-black font-playfair text-[#2D1B14] mb-2">No question available</h2>
            <p className="text-sm italic text-[#2D1B14]/60">Check that the backend has ingested visual questions.</p>
          </div>
        )}

        {question && (
          <>
            <div className="bg-white p-6 rounded-lg border border-[#2D1B14]/8 shadow-sm mb-8">
              <div className="text-[10px] font-bold uppercase tracking-widest opacity-40 mb-3">Observe</div>
              <div className="w-full bg-[#F5F2E7] rounded-md border border-[#2D1B14]/8 flex items-center justify-center min-h-[200px] overflow-hidden">
                {question.show_shape && question.shape_path ? (
                  <svg
                    viewBox={question.shape_view_box || '0 0 240 160'}
                    className="w-full max-h-[360px] p-4"
                    role="img"
                    aria-label="visual silhouette prompt"
                  >
                    <path d={question.shape_path} fill="#2D1B14" />
                  </svg>
                ) : (
                  <img
                    src={question.image_url}
                    alt="visual prompt"
                    className="w-full max-h-[360px] object-contain"
                  />
                )}
              </div>
            </div>

            <div className="bg-white p-8 rounded-lg border border-[#2D1B14]/8 shadow-sm mb-8">
              <div className="flex items-start justify-between gap-6">
                <div className="flex-1">
                  <h2 className="text-2xl font-black font-playfair text-[#2D1B14] leading-relaxed">{question.text}</h2>
                  <div className="mt-4 text-xs text-[#2D1B14]/60 italic">Topic: {question.topic}</div>
                </div>
                <button
                  onClick={handleHint}
                  disabled={!!hint || isHintLoading || isAnswered}
                  className={`px-4 py-3 rounded-md border text-[10px] font-bold uppercase tracking-widest flex items-center gap-2 transition-colors ${
                    hint ? 'opacity-50 cursor-not-allowed border-[#2D1B14]/8' : 'border-[#D4AF37]/50 hover:bg-[#D4AF37]/10'
                  }`}
                >
                  {isHintLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Lightbulb className="w-4 h-4 text-[#D4AF37]" />}
                  Hint
                </button>
              </div>

              {hint && (
                <div className="mt-6 p-5 bg-[#F5F2E7] rounded-md border border-[#2D1B14]/8">
                  <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-widest opacity-50 mb-2">
                    <BookOpen className="w-4 h-4" /> Hint
                  </div>
                  <div className="text-sm italic text-[#2D1B14]/70 leading-relaxed">{hint}</div>
                </div>
              )}
            </div>

            <div className="bg-white p-8 rounded-lg border border-[#2D1B14]/8 shadow-sm">
              {!isTextMode && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {question.options.map((opt, idx) => {
                    const isSelected = selectedAnswer === opt;
                    const isCorrect = isAnswered && feedback?.correct_answer === opt;
                    const isWrong = isAnswered && isSelected && feedback?.correct_answer !== opt;
                    return (
                      <button
                        key={idx}
                        disabled={isAnswered}
                        onClick={() => void handleSubmit(opt)}
                        className={`p-6 rounded-lg text-left border transition-all duration-200 flex items-center justify-between group ${
                          isCorrect
                            ? 'bg-green-50 border-green-500 text-green-900 shadow-sm'
                            : isWrong
                              ? 'bg-red-50 border-red-500 text-red-900 shadow-sm'
                              : isSelected
                                ? 'bg-[#D4AF37]/10 border-[#D4AF37] shadow-sm'
                                : 'bg-white border-[#2D1B14]/8 hover:border-[#D4AF37]/50 hover:shadow-sm'
                        }`}
                      >
                        <span className="font-bold text-sm">{opt}</span>
                      </button>
                    );
                  })}
                </div>
              )}

              {isTextMode && (
                <div className="space-y-4">
                  <input
                    value={textAnswer}
                    disabled={isAnswered}
                    onChange={e => setTextAnswer(e.target.value)}
                    placeholder="Type your answer..."
                    className="w-full rounded-md border border-[#2D1B14]/15 p-4 text-sm focus:border-[#D4AF37] outline-none"
                  />
                  <button
                    onClick={() => void handleSubmit(textAnswer)}
                    disabled={isAnswered}
                    className="px-6 py-4 rounded-md bg-[#2D1B14] text-[#F5F2E7] text-[10px] font-bold uppercase tracking-widest hover:bg-[#3d261c] transition-colors shadow-sm"
                  >
                    Submit
                  </button>
                </div>
              )}

              {feedback && (
                <div className="mt-8 border-t border-[#2D1B14]/8 pt-8">
                  <div className={`p-6 rounded-lg border ${feedback.is_correct ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'}`}>
                    <div className="flex items-center gap-3 mb-3">
                      {feedback.is_correct
                        ? <CheckCircle2 className="w-5 h-5 text-green-600" />
                        : <XCircle className="w-5 h-5 text-red-600" />}
                      <div className="text-sm font-bold uppercase tracking-widest">{feedback.is_correct ? 'Correct' : 'Incorrect'}</div>
                    </div>
                    <div className="text-sm text-[#2D1B14]/70 mb-3">
                      <span className="font-bold">Correct answer:</span> {feedback.correct_answer}
                    </div>
                    <div className="text-sm italic text-[#2D1B14]/70 leading-relaxed">{feedback.explanation}</div>
                  </div>

                  <div className="mt-6 flex justify-end">
                    <button
                      onClick={() => void handleNext()}
                      className="px-8 py-4 rounded-md bg-[#D4AF37] text-[#2D1B14] font-bold uppercase tracking-widest text-xs hover:bg-[#e6c75a] transition-colors"
                    >
                      Next
                    </button>
                  </div>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </InternalLayout>
  );
};

export default VisualRoomQuiz;
