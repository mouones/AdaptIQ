/**
 * src/pages/ClassicRoom.tsx
 *
 * Adaptive classic quiz experience with timed questions, hinting, and feedback loop.
 */

import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import InternalLayout from '../components/InternalLayout';
import { 
  ArrowLeft, 
  Sparkle, 
  ChevronRight, 
  Flame, 
  BookMarked, 
  ShieldQuestion,
  Lightbulb,
  CheckCircle2,
  XCircle,
  Trophy,
  Loader2,
  Compass,
  BookOpen,
  ArrowRight
} from 'lucide-react';
import { TopicType, Question, QuizSessionState } from '../types';
import { generateQuestion, generateHint, submitAnswer, resetSession } from '../services/apiService';
import { TIMER_SECONDS } from '../config';

const isQuestionReady = (q: Question | null | undefined): q is Question =>
  Boolean(q?.text?.trim()) && Array.isArray(q?.options) && q.options.length >= 2;

// Render classic-room flow: topic selection, quiz loop, and summary screen.
const ClassicRoom: React.FC = () => {
  const navigate = useNavigate();
  const [step, setStep] = useState<'selection' | 'quiz' | 'summary'>('selection');
  const [topic, setTopic] = useState<TopicType | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [session, setSession] = useState<QuizSessionState | null>(null);
  const [currentQuestion, setCurrentQuestion] = useState<Question | null>(null);
  const [selectedAnswer, setSelectedAnswer] = useState<string | null>(null);
  const [isAnswered, setIsAnswered] = useState(false);
  const [hint, setHint] = useState<string | null>(null);
  const [isHintLoading, setIsHintLoading] = useState(false);
  const [difficulty, setDifficulty] = useState(2);
  const [timeLeft, setTimeLeft] = useState(TIMER_SECONDS);
  const [displayTimeLeft, setDisplayTimeLeft] = useState(TIMER_SECONDS);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [answerFeedback, setAnswerFeedback] = useState<{
    is_correct: boolean;
    correct_answer: string;
    explanation: string;
    next_question?: Question;
  } | null>(null);

  // Timer effect
  useEffect(() => {
    if (step !== 'quiz' || isLoading || isAnswered || !isQuestionReady(currentQuestion)) return;
    if (timeLeft <= 0) {
      handleAnswer('');
      return;
    }
    const timer = setInterval(() => {
      setTimeLeft((prev) => prev - 1);
    }, 1000);
    return () => clearInterval(timer);
  }, [step, isLoading, isAnswered, timeLeft, currentQuestion]);

  useEffect(() => {
    if (isAnswered) return;
    setDisplayTimeLeft(timeLeft);
  }, [timeLeft, isAnswered]);

  // Start a new classic quiz session and load the first question.
  const startSession = async (selectedTopic: TopicType) => {
    setIsLoading(true);
    setLoadError(null);
    setTopic(selectedTopic);
    setCurrentQuestion(null);
    setSelectedAnswer(null);
    setIsAnswered(false);
    setAnswerFeedback(null);
    setHint(null);
    setTimeLeft(TIMER_SECONDS);
    setDisplayTimeLeft(TIMER_SECONDS);
    try {
      resetSession();
      const firstQuestion = await generateQuestion(selectedTopic, difficulty);
      if (!isQuestionReady(firstQuestion)) {
        throw new Error('Received an incomplete question from the server.');
      }
      setCurrentQuestion(firstQuestion);
      setTimeLeft(TIMER_SECONDS);
      setDisplayTimeLeft(TIMER_SECONDS);
      setSession({
        topic: selectedTopic,
        questions: [firstQuestion],
        currentIndex: 0,
        score: 0,
        pointsEarned: 0,
        hintsUsed: 0,
        startTime: Date.now(),
        isFinished: false
      });
      setStep('quiz');
    } catch (error) {
      console.error("Failed to start session:", error);
      setLoadError(error instanceof Error ? error.message : 'Unable to start this session.');
    } finally {
      setIsLoading(false);
    }
  };

 // Submit user answer and process backend scoring/feedback response.
 const handleAnswer = (answer: string) => {
    if (isAnswered || isLoading || !isQuestionReady(currentQuestion)) return;
    setSelectedAnswer(answer);
    setIsAnswered(true);

    const timeTaken = TIMER_SECONDS - timeLeft;

    // Submit to backend for verification
    if (currentQuestion) {
      submitAnswer({
        question_id: currentQuestion.id,
        ...(answer ? { selected_answer: answer } : { selected_index: -1 }),
        time_taken: timeTaken,
        used_hint: !!hint,
      }).then(result => {
        setAnswerFeedback({
          is_correct: result.is_correct,
          correct_answer: result.correct_answer,
          explanation: result.explanation,
          next_question: result.next_question,
        });

        // Update difficulty based on correctness
        if (result.success) {
          const wasCorrect = result.is_correct;

          if (wasCorrect) {
            setDifficulty(prev => Math.min(prev + 1, 5));
            setSession(prev => prev ? {
              ...prev,
              score: prev.score + 1,
              pointsEarned: prev.pointsEarned + 10 + Math.floor(timeLeft / 3)
            } : null);
          } else {
            setDifficulty(Math.max(result.updated_difficulty, 1));
            setSession(prev => prev ? {
              ...prev,
              pointsEarned: Math.max(0, prev.pointsEarned - 5)
            } : null);
          }
        }
      }).catch(err => console.warn('submitAnswer failed (non-fatal):', err));
    }
  };

  // Request contextual hint for the current question.
  const handleHint = async () => {
    if (!currentQuestion || hint || isHintLoading) return;
    setIsHintLoading(true);
    try {
      const hintText = await generateHint(currentQuestion.text, currentQuestion.id);
      setHint(hintText);
      setSession(prev => prev ? {
        ...prev,
        pointsEarned: prev.pointsEarned - 3,
        hintsUsed: prev.hintsUsed + 1
      } : null);
    } catch (error) {
      console.error("Failed to get hint:", error);
    } finally {
      setIsHintLoading(false);
    }
  };

  // Advance to next question or finish session when limit is reached.
  const nextQuestion = async () => {
    if (!session || !topic) return;
    
    if (session.currentIndex >= 9) {
      setStep('summary');
      return;
    }

    setIsLoading(true);
    setLoadError(null);

    try {
      const nextQ = answerFeedback?.next_question ?? await generateQuestion(topic, difficulty);
      if (!isQuestionReady(nextQ)) {
        throw new Error('Received an incomplete question from the server.');
      }
      setCurrentQuestion(nextQ);
      setSession(prev => prev ? {
        ...prev,
        currentIndex: prev.currentIndex + 1,
        questions: [...prev.questions, nextQ]
      } : null);
      setIsAnswered(false);
      setSelectedAnswer(null);
      setAnswerFeedback(null);
      setHint(null);
      setTimeLeft(TIMER_SECONDS);
      setDisplayTimeLeft(TIMER_SECONDS);
    } catch (error) {
      console.error("Failed to load next question:", error);
      const message = error instanceof Error ? error.message : String(error);
      if (/no more questions available/i.test(message)) {
        setSession(prev => prev ? { ...prev, isFinished: true } : prev);
        setStep('summary');
      } else {
        setLoadError(message);
      }
    } finally {
      setIsLoading(false);
    }
  };

  const retryLoadQuestion = async () => {
    if (!session || !topic) return;
    setIsLoading(true);
    setLoadError(null);
    try {
      const nextQ = await generateQuestion(topic, difficulty);
      if (!isQuestionReady(nextQ)) {
        throw new Error('Received an incomplete question from the server.');
      }
      setCurrentQuestion(nextQ);
      setIsAnswered(false);
      setSelectedAnswer(null);
      setAnswerFeedback(null);
      setHint(null);
      setTimeLeft(TIMER_SECONDS);
      setDisplayTimeLeft(TIMER_SECONDS);
    } catch (error) {
      console.error('Failed to retry question load:', error);
      setLoadError(error instanceof Error ? error.message : 'Unable to load the question.');
    } finally {
      setIsLoading(false);
    }
  };

  if (step === 'selection') {
    return (
      <InternalLayout>
        <button onClick={() => navigate('/dashboard')} className="flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[#2D1B14]/60 hover:text-[#D4AF37] mb-12 transition-colors">
          <ArrowLeft className="w-4 h-4" /> Back to Dashboard
        </button>

        <div className="max-w-6xl mx-auto text-center">
          <div className="inline-block mb-8">
            <div className="flex items-center gap-3 px-5 py-2 border border-[#D4AF37]/40 rounded-full text-[11px] font-bold uppercase tracking-[0.4em] text-[#D4AF37]">
              <Sparkle className="w-3 h-3 fill-current" /> Classic Room
            </div>
          </div>
          
          <h1 className="text-5xl font-black font-playfair text-[#2D1B14] mb-6">Choose Your Path</h1>
          <p className="text-xl text-[#2D1B14]/60 italic mb-12">Select the domain of knowledge you wish to explore today.</p>

          <div className="grid grid-cols-1 gap-6 mb-12">
            {[
              { id: 'History', icon: <BookMarked className="w-6 h-6" />, desc: 'Delve into the chronicles of human civilization.' },
              { id: 'Geography', icon: <ShieldQuestion className="w-6 h-6" />, desc: 'Master the logic of shapes and space.' },
              { id: 'Mixed', icon: <Flame className="w-6 h-6" />, desc: 'A broad examination of various scholarly disciplines.' }
            ].map((t) => (
              <button
                key={t.id}
                onClick={() => startSession(t.id as TopicType)}
                disabled={isLoading}
                className="group relative bg-white p-8 rounded-lg border border-[#2D1B14]/8 hover:border-[#D4AF37] transition-all duration-300 text-left flex items-center gap-8 shadow-sm hover:shadow-xl hover:-translate-y-0.5"
              >
                <div className="w-16 h-16 bg-[#F5F2E7] rounded-md flex items-center justify-center text-[#2D1B14] group-hover:bg-[#2D1B14] group-hover:text-[#D4AF37] transition-all">
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

          {isLoading && (
            <div className="flex items-center justify-center gap-3 text-[#D4AF37] font-bold uppercase tracking-widest text-xs">
              <Loader2 className="w-5 h-5 animate-spin" /> Preparing the Archives...
            </div>
          )}
        </div>
      </InternalLayout>
    );
  }

  if (step === 'quiz' && currentQuestion && session) {
    return (
      <InternalLayout>
        <div className="max-w-7xl mx-auto">
          {/* Quiz Header */}
          <div className="flex justify-between items-center mb-12 pb-6 border-b border-[#D4AF37]/20">
            <div className="flex items-center gap-4">
              <div className="text-[10px] font-bold uppercase tracking-[0.3em] text-[#D4AF37]">Question {session.currentIndex + 1} / 10</div>
              <div className="w-32 h-1.5 bg-[#2D1B14]/5 rounded-full overflow-hidden">
                <div className="h-full bg-[#D4AF37] transition-all" style={{ width: `${(session.currentIndex + 1) * 10}%` }} />
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
                <div className="text-xs font-bold uppercase tracking-widest opacity-40">Points</div>
                <div className="text-xl font-black font-playfair text-[#2D1B14]">{session.pointsEarned}</div>
              </div>
              <div className="text-center">
                <div className="text-xs font-bold uppercase tracking-widest opacity-40">Difficulty</div>
                <div className="flex gap-1 mt-1">
                  {[1,2,3,4,5].map(i => (
                    <div key={i} className={`w-1.5 h-1.5 rounded-full ${i <= difficulty ? 'bg-[#D4AF37]' : 'bg-[#2D1B14]/10'}`} />
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* Question Area */}
          <div className="mb-12">
            {loadError ? (
              <div className="p-8 bg-red-50 rounded-lg border border-red-200 text-center">
                <p className="text-red-800 mb-4">{loadError}</p>
                <button
                  onClick={() => void retryLoadQuestion()}
                  disabled={isLoading}
                  className="px-6 py-3 rounded-md bg-[#2D1B14] text-[#F5F2E7] text-[10px] font-bold uppercase tracking-[0.2em] hover:bg-[#3d261c] transition-all"
                >
                  {isLoading ? 'Retrying…' : 'Retry Question'}
                </button>
              </div>
            ) : isLoading ? (
              <div className="flex flex-col items-center justify-center py-16">
                <Loader2 className="w-8 h-8 animate-spin text-[#D4AF37] mb-4" />
                <p className="text-[#2D1B14]/50 italic">Preparing your next question…</p>
              </div>
            ) : (
              <>
            <h2 className="text-3xl font-black font-playfair text-[#2D1B14] mb-10 leading-relaxed">
              {currentQuestion.text}
            </h2>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {currentQuestion.options.map((option, idx) => {
                const isSelected = selectedAnswer === option;
                const isCorrect = isAnswered && !!answerFeedback && option.trim() === answerFeedback.correct_answer.trim();
                const isWrong = isAnswered && !!answerFeedback && isSelected && option.trim() !== answerFeedback.correct_answer.trim();

                return (
                  <button
                    key={idx}
                    disabled={isAnswered}
                    onClick={() => handleAnswer(option)}
                    className={`p-6 rounded-lg text-left border transition-all duration-200 flex items-center justify-between group ${
                      isCorrect ? 'bg-green-50 border-green-500 text-green-900 shadow-sm' :
                      isWrong ? 'bg-red-50 border-red-500 text-red-900 shadow-sm' :
                      isSelected ? 'border-[#D4AF37] bg-[#FDFCF7] shadow-sm' :
                      isAnswered ? 'border-[#2D1B14]/10 opacity-50' :
                      'bg-white border-[#2D1B14]/8 hover:border-[#D4AF37]/50 hover:shadow-sm'
                    }`}
                  >
                    <span className="font-serif italic">{option}</span>
                    {isCorrect && <CheckCircle2 className="w-5 h-5 text-green-600 shrink-0" />}
                    {isWrong && <XCircle className="w-5 h-5 text-red-600 shrink-0" />}
                  </button>
                );
              })}
            </div>
            </>
            )}
          </div>

          {/* Hint & Explanation */}
          <div className="space-y-6">
            {!loadError && !isAnswered && !isLoading && (
              <div className="flex justify-center">
                <button
                  onClick={handleHint}
                  disabled={isHintLoading || !!hint}
                  className="flex items-center gap-2 px-6 py-3 rounded-md border border-[#D4AF37]/30 text-[10px] font-bold uppercase tracking-widest text-[#D4AF37] hover:bg-[#D4AF37] hover:text-[#2D1B14] transition-all disabled:opacity-50"
                >
                  {isHintLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Lightbulb className="w-3 h-3" />}
                  {hint ? 'Hint Revealed' : 'Request Hint (-3 Points)'}
                </button>
              </div>
            )}

            {hint && !isAnswered && (
              <div className="p-6 bg-[#FDFCF7] rounded-r-lg rounded-l-sm border-l-4 border-[#D4AF37] italic text-sm text-[#2D1B14]/70 font-serif animate-in fade-in slide-in-from-top-2">
                <span className="font-bold block mb-1 text-[#D4AF37] uppercase text-[10px] tracking-widest">Archival Hint</span>
                "{hint}"
              </div>
            )}

            {(isAnswered || timeLeft === 0) && !loadError && (
              <div className="p-8 bg-white rounded-lg border border-[#2D1B14]/8 shadow-lg animate-in fade-in slide-in-from-bottom-4">
                {answerFeedback && (
                  <div className={`flex items-center gap-3 mb-4 ${answerFeedback.is_correct ? 'text-green-700' : 'text-red-700'}`}>
                    {answerFeedback.is_correct
                      ? <CheckCircle2 className="w-5 h-5" />
                      : <XCircle className="w-5 h-5" />}
                    <span className="text-xs font-bold uppercase tracking-widest">
                      {answerFeedback.is_correct ? 'Correct!' : `Wrong \u2014 Answer: ${answerFeedback.correct_answer}`}
                    </span>
                  </div>
                )}
                <div className="flex items-center gap-3 mb-4">
                  <BookOpen className="w-5 h-5 text-[#D4AF37]" />
                  <span className="text-xs font-bold uppercase tracking-widest text-[#D4AF37]">Scholarly Explanation</span>
                </div>
                <p className="text-lg font-serif italic text-[#2D1B14]/80 leading-relaxed mb-8">
                  {answerFeedback?.explanation || currentQuestion.explanation || 'Review the topic context and compare each option carefully before deciding.'}
                </p>
                <button
                  onClick={nextQuestion}
                  disabled={isLoading}
                  className="w-full py-4 rounded-md bg-[#2D1B14] text-[#F5F2E7] text-[10px] font-bold uppercase tracking-[0.3em] hover:bg-[#3d261c] transition-all flex items-center justify-center gap-2"
                >
                  {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <>Continue Journey <ArrowRight className="w-4 h-4" /></>}
                </button>
              </div>
            )}
          </div>
        </div>
      </InternalLayout>
    );
  }

  if (step === 'summary' && session) {
    const accuracy = Math.round((session.score / 10) * 100);
    return (
      <InternalLayout>
        <div className="max-w-6xl mx-auto text-center">
          <div className="w-24 h-24 bg-[#2D1B14] rounded-full flex items-center justify-center border-4 border-[#D4AF37] shadow-2xl mx-auto mb-8">
            <Trophy className="text-[#D4AF37] w-10 h-10" />
          </div>
          
          <h1 className="text-5xl font-black font-playfair text-[#2D1B14] mb-4">Session Complete</h1>
          <p className="text-xl text-[#2D1B14]/60 italic mb-12">Your contributions have been recorded in the permanent archives.</p>

          <div className="grid grid-cols-3 gap-8 mb-16">
            <div className="p-8 bg-white rounded-lg border border-[#2D1B14]/8 hover:shadow-md transition-shadow">
              <div className="text-3xl font-black font-playfair text-[#2D1B14] mb-1">{session.score}/10</div>
              <div className="text-[10px] font-bold uppercase tracking-widest opacity-40">Correct</div>
            </div>
            <div className="p-8 bg-white rounded-lg border border-[#2D1B14]/8 hover:shadow-md transition-shadow">
              <div className="text-3xl font-black font-playfair text-[#2D1B14] mb-1">{accuracy}%</div>
              <div className="text-[10px] font-bold uppercase tracking-widest opacity-40">Accuracy</div>
            </div>
            <div className="p-8 bg-white rounded-lg border border-[#2D1B14]/8 hover:shadow-md transition-shadow">
              <div className="text-3xl font-black font-playfair text-[#2D1B14] mb-1">+{session.pointsEarned}</div>
              <div className="text-[10px] font-bold uppercase tracking-widest opacity-40">Points Earned</div>
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
                setTopic(null);
                setSession(null);
                setCurrentQuestion(null);
                setSelectedAnswer(null);
                setIsAnswered(false);
                setAnswerFeedback(null);
                setHint(null);
                setLoadError(null);
                setTimeLeft(TIMER_SECONDS);
                setDisplayTimeLeft(TIMER_SECONDS);
                setDifficulty(2);
              }}
              className="px-10 py-5 rounded-md bg-[#2D1B14] text-[#F5F2E7] text-[10px] font-bold uppercase tracking-[0.3em] hover:bg-[#3d261c] transition-all"
            >
              Start New Session
            </button>
          </div>
        </div>
      </InternalLayout>
    );
  }

  return null;
};

export default ClassicRoom;
