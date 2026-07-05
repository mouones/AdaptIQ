/**
 * src/pages/CustomRoom.tsx
 *
 * Custom-room flow for topic-specific practice across history and geography.
 * Includes catalogue-driven selection, timed quiz loop, hints, and mastery summary.
 */

import React, { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'motion/react';
import {
  History,
  Globe,
  ArrowLeft,
  ChevronRight,
  BookOpen,
  Trophy,
  Sparkle,
  Loader2,
  CheckCircle2,
  XCircle,
  ShieldQuestion,
  BookMarked,
  ArrowRight,
  Map as MapIcon,
} from 'lucide-react';
import InternalLayout from '../components/InternalLayout';
import type {
  CustomSessionState,
  CustomQuestion,
} from '../types/custom';
import {
  type CustomTopic,
  startCustomSession,
  generateCustomQuestion,
  submitCustomAnswer,
  endCustomSession,
  getCustomTopics,
} from '../services/customService';
import { API_BASE, TIMER_SECONDS } from '../config';
import { authFetch } from '../services/http';

const cleanTopicDescription = (description: string): string => {
  return (description || '').trim();
};

const customTopicLabel = (topic: CustomTopic): string => {
  const type = (topic.type || '').trim();
  const name = (topic.name || '').trim();
  if (!type || !name) return name || type;
  if (name.toLowerCase().startsWith(type.toLowerCase())) return name;
  return `${type} - ${name}`;
};

const CUSTOM_FIXED_LEVEL = 3; // Custom Room has no adaptive levels; keep standard 4-option MCQ.

// - Component -

// Render custom-room multi-screen flow and session lifecycle.
const CustomRoom: React.FC = () => {
  const navigate = useNavigate();

  // Screen state
  const [screen, setScreen] = useState<'main' | 'history' | 'geography' | 'quiz' | 'summary'>('main');
  const [selectedCountry, setSelectedCountry] = useState<string | null>(null);
  const [topicCatalogue, setTopicCatalogue] = useState<CustomTopic[]>([]);
  const [isCatalogueLoading, setIsCatalogueLoading] = useState(false);
  const [catalogueError, setCatalogueError] = useState<string | null>(null);

  // Session state (populated once startQuiz resolves)
  const [session, setSession] = useState<CustomSessionState | null>(null);

  // Per-question UI state
  const [selectedAnswer, setSelectedAnswer] = useState<string>('');
  const [isAnswered, setIsAnswered] = useState(false);
  const [feedback, setFeedback] = useState<{ is_correct: boolean; correct_answer: string; explanation: string; new_progress_percentage: number } | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const [isHintLoading, setIsHintLoading] = useState(false);

  // Loading flags
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Timer
  const [timeLeft, setTimeLeft] = useState(TIMER_SECONDS);
  const [displayTimeLeft, setDisplayTimeLeft] = useState(TIMER_SECONDS);

  // Summary data (returned by endCustomSession)
  const [summary, setSummary] = useState<{
    questions_answered: number;
    correct_count: number;
    completion_percentage_after: number;
    timeSpentMin: number;
  } | null>(null);

  const historyTopics = useMemo(
    () => topicCatalogue.filter((topic) => topic.type.toLowerCase() === 'history'),
    [topicCatalogue],
  );

  const geographyTopics = useMemo(
    () => topicCatalogue.filter((topic) => topic.type.toLowerCase() === 'geography'),
    [topicCatalogue],
  );

  const additionalTopics = useMemo(
    () => topicCatalogue.filter((topic) => {
      const type = topic.type.toLowerCase();
      return type !== 'history' && type !== 'geography';
    }),
    [topicCatalogue],
  );

  const selectedGeographyTopic = useMemo(
    () => geographyTopics.find((topic) => topic.name === selectedCountry) ?? null,
    [geographyTopics, selectedCountry],
  );

  const loadTopicCatalogue = async () => {
    setIsCatalogueLoading(true);
    setCatalogueError(null);
    try {
      const response = await getCustomTopics();
      setTopicCatalogue(Array.isArray(response.topics) ? response.topics : []);
    } catch (err) {
      setTopicCatalogue([]);
      const message = err instanceof Error ? err.message : 'Unable to load topic catalogue at this time.';
      setCatalogueError(message);
    } finally {
      setIsCatalogueLoading(false);
    }
  };

  useEffect(() => {
    void loadTopicCatalogue();
  }, []);

  useEffect(() => {
    if (selectedCountry && !geographyTopics.some((topic) => topic.name === selectedCountry)) {
      setSelectedCountry(null);
    }
  }, [geographyTopics, selectedCountry]);

  // - Timer -

  useEffect(() => {
    const activeQuestion = session?.questions[session.currentIndex];
    if (screen !== 'quiz' || isLoading || isAnswered || loadError || !isQuestionReady(activeQuestion)) return;
    if (timeLeft <= 0) {
      void handleAnswerSubmit('__timeout__');
      return;
    }
    const timer = setInterval(() => setTimeLeft(prev => prev - 1), 1000);
    return () => clearInterval(timer);
  }, [screen, isLoading, isAnswered, timeLeft]);

  useEffect(() => {
    if (isAnswered) return;
    setDisplayTimeLeft(timeLeft);
  }, [timeLeft, isAnswered]);

  const isQuestionReady = (q: CustomQuestion | undefined): q is CustomQuestion =>
    Boolean(q?.text?.trim()) && (q?.is_free_text || (Array.isArray(q?.options) && q.options.length >= 2));

  // - Reset per-question state -

  const resetAnswerState = () => {
    setSelectedAnswer('');
    setIsAnswered(false);
    setFeedback(null);
    setHint(null);
    setLoadError(null);
  };

  const resetQuestionState = () => {
    resetAnswerState();
    setTimeLeft(TIMER_SECONDS);
    setDisplayTimeLeft(TIMER_SECONDS);
  };

  // - Start quiz -

  // Start backend session, load first question, and move to quiz screen.
  const startQuiz = async (topic: string, conceptId?: string, conceptName?: string) => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const sessionData = await startCustomSession(topic, conceptId);
      const firstQ = await generateCustomQuestion(sessionData.session_id, topic, conceptId, CUSTOM_FIXED_LEVEL);

      if (!isQuestionReady(firstQ)) {
        throw new Error('Received an incomplete question from the server.');
      }

      setSession({
        session_id:         sessionData.session_id,
        topic,
        concept_id:         sessionData.concept_id ?? conceptId,
        concept_name:       conceptName,
        questions:          [firstQ],
        currentIndex:       0,
        score:              0,
        pointsEarned:       0,
        hintsUsed:          0,
        startTime:          Date.now(),
        isFinished:         false,
        progress_percentage: sessionData.progress_percentage,
        currentLevel:       CUSTOM_FIXED_LEVEL,
        streakCorrect:      0,
        streakWrong:        0,
      });

      resetQuestionState();
      setScreen('quiz');
    } catch (err) {
      console.error('Failed to start quiz:', err);
      setLoadError(err instanceof Error ? err.message : 'Unable to start this custom inquiry.');
    } finally {
      setIsLoading(false);
    }
  };

  // - Submit answer -

  // Submit selected answer and update score/progress feedback.
  const handleAnswerSubmit = async (answer: string) => {
    if (isAnswered || !session || isLoading) return;
    const currentQ = session.questions[session.currentIndex];
    if (!isQuestionReady(currentQ)) return;
    const answerToSubmit = (answer || selectedAnswer || '').trim();
    if (!answerToSubmit) return;

    setSelectedAnswer(answerToSubmit);
    setIsAnswered(true);

    try {
      const currentQ = session.questions[session.currentIndex];
      const result = await submitCustomAnswer({
        session_id:     session.session_id,
        question_id:    currentQ.id,
        answer:         answerToSubmit,
        used_hint:      !!hint,
        time_taken:     Math.max(0, TIMER_SECONDS - timeLeft),
      });

      setFeedback({
        is_correct:              result.is_correct,
        correct_answer:          result.correct_answer,
        explanation:             result.explanation,
        new_progress_percentage: result.new_progress_percentage,
      });

      setSession(prev => {
        if (!prev) return null;
        return {
          ...prev,
          score:               result.is_correct ? prev.score + 1 : prev.score,
          pointsEarned:        result.is_correct ? prev.pointsEarned + 10 : prev.pointsEarned,
          progress_percentage: result.new_progress_percentage,
          currentLevel:        CUSTOM_FIXED_LEVEL,
          streakCorrect:       0,
          streakWrong:         0,
        };
      });
    } catch (err) {
      console.error('Submit answer failed:', err);
      // Still mark as answered so the user can continue
      setFeedback({
        is_correct:              false,
        correct_answer:          'Unavailable',
        explanation:             'Unable to grade this answer right now. Please continue to the next question.',
        new_progress_percentage: session.progress_percentage,
      });
    }
  };

  // - Next question -

  // Advance to next question or finish session when question limit is reached.
  const handleNextQuestion = async () => {
    if (!session) return;

    const QUESTIONS_PER_SESSION = 10;

    if (session.currentIndex >= QUESTIONS_PER_SESSION - 1) {
      // Session done - call end endpoint
      setIsLoading(true);
      try {
        const endData = await endCustomSession(session.session_id);
        setSummary({
          questions_answered:         endData.questions_answered,
          correct_count:              endData.correct_count,
          completion_percentage_after: endData.completion_percentage_after,
          timeSpentMin:               Math.round((Date.now() - session.startTime) / 1000 / 60),
        });
      } catch (err) {
        console.error('End session failed:', err);
        // Fallback summary from local state
        setSummary({
          questions_answered:         session.questions.length,
          correct_count:              session.score,
          completion_percentage_after: session.progress_percentage,
          timeSpentMin:               Math.round((Date.now() - session.startTime) / 1000 / 60),
        });
      } finally {
        setIsLoading(false);
      }
      setScreen('summary');
      return;
    }

    setIsLoading(true);
    setLoadError(null);
    try {
      const nextQ = await generateCustomQuestion(session.session_id, session.topic, session.concept_id, CUSTOM_FIXED_LEVEL);
      if (!isQuestionReady(nextQ)) {
        throw new Error('Received an incomplete question from the server.');
      }
      setSession(prev => {
        if (!prev) return null;
        return {
          ...prev,
          currentIndex: prev.currentIndex + 1,
          questions:    [...prev.questions, nextQ],
        };
      });
      resetQuestionState();
    } catch (err) {
      console.error('Failed to load next question:', err);
      setLoadError(err instanceof Error ? err.message : 'Unable to load the next question.');
    } finally {
      setIsLoading(false);
    }
  };

  const retryLoadQuestion = async () => {
    if (!session) return;
    if (session.currentIndex >= 9) {
      await handleNextQuestion();
      return;
    }
    setIsLoading(true);
    setLoadError(null);
    try {
      const nextQ = await generateCustomQuestion(session.session_id, session.topic, session.concept_id, CUSTOM_FIXED_LEVEL);
      if (!isQuestionReady(nextQ)) {
        throw new Error('Received an incomplete question from the server.');
      }
      const isNewIndex = session.questions.length <= session.currentIndex;
      setSession(prev => {
        if (!prev) return null;
        if (isNewIndex) {
          return {
            ...prev,
            currentIndex: prev.currentIndex + 1,
            questions:    [...prev.questions, nextQ],
          };
        }
        const updated = [...prev.questions];
        updated[prev.currentIndex] = nextQ;
        return { ...prev, questions: updated };
      });
      resetQuestionState();
    } catch (err) {
      console.error('Failed to retry question load:', err);
      setLoadError(err instanceof Error ? err.message : 'Unable to load the question.');
    } finally {
      setIsLoading(false);
    }
  };

  // - Hint -
  // Request one hint for the active custom-room question.
  const handleGetHint = async () => {
    if (!session || isHintLoading || hint) return;
    setIsHintLoading(true);
    try {
      const currentQ = session.questions[session.currentIndex];
      const res = await authFetch(`${API_BASE}/api/custom/generate-hint`, {
        method: 'POST',
        body: JSON.stringify({ question_id: currentQ.id, question_text: currentQ.text }),
      });
      const data = await res.json();
      setHint(data.hint ?? 'No hint available.');
      setSession(prev => prev ? { ...prev, hintsUsed: prev.hintsUsed + 1 } : null);
    } catch {
      setHint('Unable to retrieve a hint at this time.');
    } finally {
      setIsHintLoading(false);
    }
  };

  // -
  // Render helpers
  // -

  // Render main category selection screen.
  const renderMain = () => (
    <div className="max-w-7xl mx-auto text-center">
      <div className="mb-12">
        <h1 className="text-5xl font-black font-playfair text-[#2D1B14] mb-4">Custom Archives</h1>
        <p className="text-xl text-[#2D1B14]/60 italic">Select your field of scholarly inquiry.</p>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
        {/* History */}
        <button
          onClick={() => setScreen('history')}
          className="group relative bg-white p-12 rounded-lg border border-[#2D1B14]/8 hover:border-[#D4AF37] transition-all duration-500 shadow-sm hover:shadow-2xl overflow-hidden hover:-translate-y-1"
        >
          <div className="absolute top-0 left-0 w-full h-1 bg-[#D4AF37] transform -translate-x-full group-hover:translate-x-0 transition-transform duration-500" />
          <div className="w-20 h-20 bg-[#F5F2E7] rounded-full flex items-center justify-center mx-auto mb-8 group-hover:scale-110 transition-transform duration-500">
            <History className="w-10 h-10 text-[#2D1B14]" />
          </div>
          <h2 className="text-3xl font-black font-playfair text-[#2D1B14] mb-4">History</h2>
          <p className="text-[#2D1B14]/60 italic">Traverse the corridors of time and relive the great epochs of humanity.</p>
          <div className="mt-8 flex items-center justify-center gap-2 text-[10px] font-bold uppercase tracking-[0.3em] text-[#D4AF37] opacity-0 group-hover:opacity-100 transition-opacity">
            Enter Archives <ChevronRight className="w-4 h-4" />
          </div>
        </button>

        {/* Geography */}
        <button
          onClick={() => setScreen('geography')}
          className="group relative bg-white p-12 rounded-lg border border-[#2D1B14]/8 hover:border-[#D4AF37] transition-all duration-500 shadow-sm hover:shadow-2xl overflow-hidden hover:-translate-y-1"
        >
          <div className="absolute top-0 left-0 w-full h-1 bg-[#D4AF37] transform -translate-x-full group-hover:translate-x-0 transition-transform duration-500" />
          <div className="w-20 h-20 bg-[#F5F2E7] rounded-full flex items-center justify-center mx-auto mb-8 group-hover:scale-110 transition-transform duration-500">
            <Globe className="w-10 h-10 text-[#2D1B14]" />
          </div>
          <h2 className="text-3xl font-black font-playfair text-[#2D1B14] mb-4">Geography</h2>
          <p className="text-[#2D1B14]/60 italic">Chart the vast expanses of our world and master the lore of nations.</p>
          <div className="mt-8 flex items-center justify-center gap-2 text-[10px] font-bold uppercase tracking-[0.3em] text-[#D4AF37] opacity-0 group-hover:opacity-100 transition-opacity">
            Browse Atlas <ChevronRight className="w-4 h-4" />
          </div>
        </button>
      </div>
      {additionalTopics.length > 0 && (
        <div className="mt-12 text-left">
          <h2 className="text-xs font-bold uppercase tracking-[0.3em] text-[#D4AF37] mb-4">
            Additional Topics
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {additionalTopics.map((topic) => (
              <button
                key={topic.slug}
                onClick={() => startQuiz(customTopicLabel(topic))}
                className="group bg-white p-8 rounded-lg border border-[#2D1B14]/8 hover:border-[#D4AF37] transition-all text-left shadow-sm hover:shadow-xl hover:-translate-y-0.5 duration-300"
              >
                <div className="w-12 h-12 bg-[#F5F2E7] rounded-md flex items-center justify-center mb-6 group-hover:bg-[#D4AF37] group-hover:text-[#2D1B14] transition-colors">
                  <BookMarked className="w-6 h-6" />
                </div>
                <div className="text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/40 mb-2">
                  {topic.type}
                </div>
                <h3 className="text-xl font-black font-playfair text-[#2D1B14] mb-2">{topic.name}</h3>
                <p className="text-sm text-[#2D1B14]/60 italic leading-relaxed">
                  {cleanTopicDescription(topic.description)}
                </p>
                <div className="mt-4 text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/40">
                  {topic.total_facts.toLocaleString()} indexed facts
                </div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );

  // Render history-theme selection screen.
  const renderHistory = () => (
    <div className="max-w-7xl mx-auto">
      <button
        onClick={() => setScreen('main')}
        className="flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[#2D1B14]/60 hover:text-[#D4AF37] mb-12 transition-colors"
      >
        <ArrowLeft className="w-4 h-4" /> Back to Custom Room
      </button>
      <div className="mb-12">
        <h2 className="text-4xl font-black font-playfair text-[#2D1B14] mb-2">Historical Themes</h2>
        <p className="text-[#2D1B14]/60 italic">Choose a specific era to focus your scholarly research.</p>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {isCatalogueLoading && (
          <div className="col-span-full p-12 bg-white border border-[#2D1B14]/10 text-center">
            <Loader2 className="w-8 h-8 animate-spin text-[#D4AF37] mx-auto mb-4" />
            <p className="text-[#2D1B14]/50 italic">Loading historical themes from the live catalogue...</p>
          </div>
        )}

        {!isCatalogueLoading && catalogueError && (
          <div className="col-span-full p-12 bg-white border border-[#2D1B14]/10 text-center">
            <p className="text-[#2D1B14]/70 mb-4">{catalogueError}</p>
            <button
              onClick={() => void loadTopicCatalogue()}
              className="px-6 py-3 border border-[#2D1B14] text-[10px] font-bold uppercase tracking-[0.2em] hover:bg-[#2D1B14] hover:text-[#F5F2E7] transition-all"
            >
              Reload Catalogue
            </button>
          </div>
        )}

        {!isCatalogueLoading && !catalogueError && historyTopics.length === 0 && (
          <div className="col-span-full p-12 border border-dashed border-[#2D1B14]/20 text-center">
            <BookOpen className="w-10 h-10 text-[#2D1B14]/20 mx-auto mb-4" />
            <p className="text-[#2D1B14]/40 italic">No history topics are currently available.</p>
          </div>
        )}

        {!isCatalogueLoading && !catalogueError && historyTopics.map((theme) => (
          <button
            key={theme.slug}
            onClick={() => startQuiz(`History - ${theme.name}`)}
            className="group bg-white p-8 rounded-lg border border-[#2D1B14]/8 hover:border-[#D4AF37] transition-all text-left shadow-sm hover:shadow-xl hover:-translate-y-0.5 duration-300"
          >
            <div className="w-12 h-12 bg-[#F5F2E7] rounded-md flex items-center justify-center mb-6 group-hover:bg-[#D4AF37] group-hover:text-[#2D1B14] transition-colors">
              <BookOpen className="w-6 h-6" />
            </div>
            <h3 className="text-xl font-black font-playfair text-[#2D1B14] mb-2">{theme.name}</h3>
            <p className="text-sm text-[#2D1B14]/60 italic leading-relaxed">{cleanTopicDescription(theme.description)}</p>
            <div className="mt-4 text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/40">
              {theme.total_facts.toLocaleString()} indexed facts
            </div>
            <div className="mt-6 text-[10px] font-bold uppercase tracking-widest text-[#D4AF37] opacity-0 group-hover:opacity-100 transition-opacity">
              Select Theme -
            </div>
          </button>
        ))}
      </div>
    </div>
  );

  // Render geography selection screen.
  const renderGeography = () => (
    <div className="max-w-7xl mx-auto">
      <button
        onClick={() => setScreen('main')}
        className="flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[#2D1B14]/60 hover:text-[#D4AF37] mb-12 transition-colors"
      >
        <ArrowLeft className="w-4 h-4" /> Back to Custom Room
      </button>
      <div className="grid lg:grid-cols-3 gap-12">
        <div className="lg:col-span-2">
          <div className="mb-8">
            <h2 className="text-4xl font-black font-playfair text-[#2D1B14] mb-2">World Cartography</h2>
            <p className="text-[#2D1B14]/60 italic">Select a nation from the live geography catalogue.</p>
          </div>
          <div className="bg-white p-8 rounded-lg border border-[#2D1B14]/8 shadow-inner min-h-[420px]">
            {isCatalogueLoading ? (
              <div className="h-full flex flex-col items-center justify-center py-16">
                <Loader2 className="w-8 h-8 animate-spin text-[#D4AF37] mb-4" />
                <p className="text-[#2D1B14]/50 italic">Loading geography catalogue...</p>
              </div>
            ) : catalogueError ? (
              <div className="h-full flex flex-col items-center justify-center py-16 text-center">
                <p className="text-[#2D1B14]/70 mb-4">{catalogueError}</p>
                <button
                  onClick={() => void loadTopicCatalogue()}
                  className="px-6 py-3 border border-[#2D1B14] text-[10px] font-bold uppercase tracking-[0.2em] hover:bg-[#2D1B14] hover:text-[#F5F2E7] transition-all"
                >
                  Reload Catalogue
                </button>
              </div>
            ) : geographyTopics.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center py-16 text-center border border-dashed border-[#2D1B14]/20">
                <Globe className="w-12 h-12 text-[#2D1B14]/20 mb-4" />
                <p className="text-[#2D1B14]/40 italic">No geography topics are currently available.</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {geographyTopics.map((country) => (
                  <button
                    key={country.slug}
                    onClick={() => setSelectedCountry(country.name)}
                    className={`group p-6 rounded-lg border transition-all duration-300 text-left ${
                      selectedCountry === country.name
                        ? 'border-[#D4AF37] bg-[#FDFCF7] shadow-lg hover:-translate-y-0.5'
                        : 'border-[#2D1B14]/8 bg-white hover:border-[#D4AF37]/60 hover:shadow-md hover:-translate-y-0.5'
                    }`}
                  >
                    <div className="w-10 h-10 bg-[#F5F2E7] rounded-full flex items-center justify-center mb-4 group-hover:bg-[#D4AF37] group-hover:text-[#2D1B14] transition-colors">
                      <MapIcon className="w-5 h-5" />
                    </div>
                    <h3 className="text-lg font-black font-playfair text-[#2D1B14] mb-2">{country.name}</h3>
                    <p className="text-sm text-[#2D1B14]/60 italic leading-relaxed">
                      {cleanTopicDescription(country.description)}
                    </p>
                    <div className="mt-4 text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/40">
                      {country.total_facts.toLocaleString()} indexed facts
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="flex flex-col justify-center">
          <AnimatePresence mode="wait">
            {selectedGeographyTopic ? (
              <motion.div
                key={selectedGeographyTopic.slug}
                initial={{ opacity: 0, x: 20 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -20 }}
                className="bg-white rounded-lg p-10 border-2 border-[#D4AF37] shadow-2xl text-center"
              >
                <div className="w-20 h-20 bg-[#F5F2E7] rounded-full flex items-center justify-center mx-auto mb-6">
                  <MapIcon className="w-10 h-10 text-[#D4AF37]" />
                </div>
                <h3 className="text-3xl font-black font-playfair text-[#2D1B14] mb-4">{selectedGeographyTopic.name}</h3>
                <div className="mb-4 text-[10px] font-bold uppercase tracking-[0.3em] text-[#D4AF37]">
                  {selectedGeographyTopic.total_facts.toLocaleString()} facts indexed
                </div>
                <p className="text-[#2D1B14]/60 italic mb-10 leading-relaxed">
                  {cleanTopicDescription(selectedGeographyTopic.description)}
                </p>
                <button
                  onClick={() => startQuiz(`Geography - ${selectedGeographyTopic.name}`)}
                  className="w-full py-4 rounded-md bg-[#2D1B14] text-[#F5F2E7] text-[10px] font-bold uppercase tracking-[0.3em] hover:bg-[#3d261c] transition-all shadow-lg"
                >
                  Begin Inquiry
                </button>
              </motion.div>
            ) : (
              <div className="text-center p-12 border border-dashed border-[#2D1B14]/20">
                <Globe className="w-12 h-12 text-[#2D1B14]/20 mx-auto mb-4" />
                <p className="text-[#2D1B14]/40 italic">Select a nation to proceed.</p>
              </div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  );

  // Render active quiz screen with answers, hints, and feedback.
  const renderQuiz = () => {
    if (!session) return null;
    const currentQ = session.questions[session.currentIndex];
    const questionReady = isQuestionReady(currentQ);

    return (
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="flex justify-between items-center mb-12 pb-6 border-b border-[#D4AF37]/20">
          <div className="flex items-center gap-6">
            <div className="w-12 h-12 bg-[#2D1B14] text-[#D4AF37] flex items-center justify-center font-black text-xl border-2 border-[#D4AF37] rounded-md">
              {session.currentIndex + 1}
            </div>
            <div>
              <div className="text-[10px] font-bold uppercase tracking-[0.3em] text-[#D4AF37]">Custom Inquiry</div>
              <div className="text-sm font-bold text-[#2D1B14]">{session.topic}</div>
              {session.concept_name && (
                <div className="text-xs text-[#2D1B14]/60">Concept: {session.concept_name}</div>
              )}
            </div>
          </div>
          <div className="flex items-center gap-8">
            {/* Progress bar */}
            <div className="text-center hidden sm:block">
              <div className="text-xs font-bold uppercase tracking-widest opacity-40 mb-1">Mastery</div>
              <div className="w-24 h-1.5 bg-[#2D1B14]/10 rounded-full overflow-hidden">
                <div
                  className="h-full bg-[#D4AF37] transition-all duration-700"
                  style={{ width: `${session.progress_percentage}%` }}
                />
              </div>
              <div className="text-xs text-[#2D1B14]/50 mt-1">{session.progress_percentage.toFixed(1)}%</div>
            </div>
            <div className="text-center">
              <div className="text-xs font-bold uppercase tracking-widest opacity-40">Time</div>
              <div className={`text-2xl font-black font-playfair ${
                !isAnswered && !isLoading && displayTimeLeft < 10 ? 'text-red-500 animate-pulse' : 'text-[#2D1B14]'
              }`}>
                {isAnswered ? '—' : isLoading ? '…' : `${displayTimeLeft}s`}
              </div>
            </div>
            <div className="text-center">
              <div className="text-xs font-bold uppercase tracking-widest opacity-40">Score</div>
              <div className="text-2xl font-black font-playfair text-[#2D1B14]">{session.score}</div>
            </div>
          </div>
        </div>

        {/* Question */}
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
          ) : isLoading || !questionReady ? (
            <div className="flex flex-col items-center justify-center py-16">
              <Loader2 className="w-8 h-8 animate-spin text-[#D4AF37] mb-4" />
              <p className="text-[#2D1B14]/50 italic">Preparing your next inquiry…</p>
            </div>
          ) : (
            <>
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
                    placeholder="Type your answer..."
                    className="w-full p-6 bg-white rounded-lg border border-[#2D1B14]/8 focus:border-[#D4AF37] text-xl font-serif italic outline-none transition-all"
                  />
                  {!isAnswered && (
                    <button
                      onClick={() => handleAnswerSubmit(selectedAnswer)}
                      disabled={!selectedAnswer.trim()}
                      className="w-full py-4 rounded-md bg-[#2D1B14] text-[#F5F2E7] text-[10px] font-bold uppercase tracking-[0.3em] hover:bg-[#3d261c] disabled:opacity-40 transition-all"
                    >
                      Submit Answer
                    </button>
                  )}
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {currentQ.options.map((option, idx) => {
                    const isSelected  = selectedAnswer === option;
                    const isCorrect   = isAnswered && feedback && option.trim() === feedback.correct_answer.trim();
                    const isWrong     = isAnswered && isSelected && feedback && option.trim() !== feedback.correct_answer.trim();

                    return (
                      <button
                        key={idx}
                        disabled={isAnswered}
                        onClick={() => handleAnswerSubmit(option)}
                        className={`p-6 rounded-lg text-left border transition-all duration-200 flex items-center justify-between group ${
                          isCorrect ? 'bg-green-50 border-green-500 text-green-900 shadow-sm' :
                          isWrong   ? 'bg-red-50 border-red-500 text-red-900 shadow-sm' :
                          isSelected ? 'border-[#D4AF37] bg-[#FDFCF7] shadow-sm' :
                          'bg-white border-[#2D1B14]/8 hover:border-[#D4AF37]/50 hover:shadow-sm'
                        }`}
                      >
                        <span className="font-serif italic">{option}</span>
                        {isCorrect && <CheckCircle2 className="w-5 h-5 text-green-600 shrink-0" />}
                        {isWrong   && <XCircle      className="w-5 h-5 text-red-600 shrink-0" />}
                      </button>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </div>

        {/* Hint & post-answer actions */}
        <div className="space-y-6">
          <AnimatePresence>
            {!loadError && isAnswered && feedback ? (
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                className="p-8 bg-white rounded-lg border border-[#2D1B14]/8 shadow-lg"
              >
                {/* Correct / Wrong banner */}
                <div className={`flex items-center gap-3 mb-4 ${feedback.is_correct ? 'text-green-700' : 'text-red-700'}`}>
                  {feedback.is_correct
                    ? <CheckCircle2 className="w-5 h-5" />
                    : <XCircle      className="w-5 h-5" />
                  }
                  <span className="text-xs font-bold uppercase tracking-widest">
                    {feedback.is_correct ? 'Correct!' : `Wrong \u2014 Answer: ${feedback.correct_answer}`}
                  </span>
                </div>

                <div className="flex items-center gap-3 mb-4">
                  <BookMarked className="w-5 h-5 text-[#D4AF37]" />
                  <span className="text-xs font-bold uppercase tracking-widest text-[#D4AF37]">Scholarly Context</span>
                </div>
                <p className="text-lg font-serif italic text-[#2D1B14]/80 leading-relaxed mb-8">
                  {feedback.explanation}
                </p>
                <button
                  onClick={handleNextQuestion}
                  disabled={isLoading}
                  className="w-full py-4 rounded-md bg-[#2D1B14] text-[#F5F2E7] text-[10px] font-bold uppercase tracking-[0.3em] hover:bg-[#3d261c] transition-all flex items-center justify-center gap-2"
                >
                  {isLoading
                    ? <Loader2 className="w-4 h-4 animate-spin" />
                    : <>Continue Research <ArrowRight className="w-4 h-4" /></>
                  }
                </button>
              </motion.div>
            ) : !isAnswered && !isLoading && questionReady && !loadError ? (
              <div className="flex flex-col items-center gap-4">
                {hint ? (
                  <motion.div
                    initial={{ opacity: 0, scale: 0.95 }}
                    animate={{ opacity: 1, scale: 1 }}
                    className="w-full p-6 bg-[#D4AF37]/5 rounded-lg border border-[#D4AF37]/20 italic text-[#2D1B14]/70 text-center"
                  >
                    <Sparkle className="w-4 h-4 text-[#D4AF37] mx-auto mb-2" />
                    "{hint}"
                  </motion.div>
                ) : (
                  <button
                    onClick={handleGetHint}
                    disabled={isHintLoading}
                    className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-widest text-[#D4AF37] hover:text-[#2D1B14] transition-colors"
                  >
                    {isHintLoading
                      ? <Loader2 className="w-3 h-3 animate-spin" />
                      : <ShieldQuestion className="w-4 h-4" />
                    }
                    Seek a Hint
                  </button>
                )}
              </div>
            ) : null}
          </AnimatePresence>
        </div>
      </div>
    );
  };

  // Render session summary after completion.
  const renderSummary = () => {
    if (!summary) return null;
    const accuracy = summary.questions_answered > 0
      ? Math.round((summary.correct_count / summary.questions_answered) * 100)
      : 0;

    return (
      <div className="max-w-6xl mx-auto text-center">
        <motion.div
          initial={{ scale: 0 }}
          animate={{ scale: 1 }}
          className="w-24 h-24 bg-[#2D1B14] rounded-full flex items-center justify-center border-4 border-[#D4AF37] shadow-2xl mx-auto mb-8"
        >
          <Trophy className="text-[#D4AF37] w-10 h-10" />
        </motion.div>
        <h1 className="text-5xl font-black font-playfair text-[#2D1B14] mb-4">Research Concluded</h1>
        <p className="text-xl text-[#2D1B14]/60 italic mb-12">Your findings have been added to the archives.</p>

        {/* Progress update */}
        <div className="mb-8 p-6 rounded-lg bg-white border border-[#D4AF37]/40">
          <div className="text-xs font-bold uppercase tracking-widest opacity-40 mb-2">Topic Mastery</div>
          <div className="w-full h-3 bg-[#2D1B14]/10 rounded-full overflow-hidden">
            <motion.div
              className="h-full bg-[#D4AF37]"
              initial={{ width: 0 }}
              animate={{ width: `${summary.completion_percentage_after}%` }}
              transition={{ duration: 1.2, ease: 'easeOut' }}
            />
          </div>
          <div className="text-2xl font-black font-playfair text-[#2D1B14] mt-2">
            {summary.completion_percentage_after.toFixed(1)}%
          </div>
        </div>

        <div className="grid grid-cols-3 gap-8 mb-16">
          <div className="p-8 bg-white rounded-lg border border-[#2D1B14]/8 hover:shadow-md transition-shadow">
            <div className="text-4xl font-black font-playfair text-[#2D1B14] mb-1">{summary.correct_count}</div>
            <div className="text-[10px] font-bold uppercase tracking-widest opacity-40">Correct</div>
          </div>
          <div className="p-8 bg-white rounded-lg border border-[#2D1B14]/8 hover:shadow-md transition-shadow">
            <div className="text-4xl font-black font-playfair text-[#2D1B14] mb-1">{accuracy}%</div>
            <div className="text-[10px] font-bold uppercase tracking-widest opacity-40">Accuracy</div>
          </div>
          <div className="p-8 bg-white rounded-lg border border-[#2D1B14]/8 hover:shadow-md transition-shadow">
            <div className="text-4xl font-black font-playfair text-[#2D1B14] mb-1">{summary.timeSpentMin}m</div>
            <div className="text-[10px] font-bold uppercase tracking-widest opacity-40">Duration</div>
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
              setScreen('main');
              setSession(null);
              setSummary(null);
              setSelectedCountry(null);
            }}
            className="px-10 py-5 rounded-md bg-[#2D1B14] text-[#F5F2E7] text-[10px] font-bold uppercase tracking-[0.3em] hover:bg-[#3d261c] transition-all"
          >
            New Custom Inquiry
          </button>
        </div>
      </div>
    );
  };

  // -
  // Main render
  // -

  return (
    <InternalLayout>
      <div className="min-h-[70vh] flex flex-col justify-center py-10">
        <AnimatePresence mode="wait">
          {isLoading && screen !== 'quiz' ? (
            <motion.div
              key="loader"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="flex flex-col items-center gap-4"
            >
              <Loader2 className="w-12 h-12 animate-spin text-[#D4AF37]" />
              <p className="text-[10px] font-bold uppercase tracking-[0.4em] text-[#2D1B14]/40">
                Consulting the Archives...
              </p>
            </motion.div>
          ) : (
            <motion.div
              key={screen}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
              transition={{ duration: 0.4 }}
            >
              {screen === 'main'      && renderMain()}
              {screen === 'history'   && renderHistory()}
              {screen === 'geography' && renderGeography()}
              {screen === 'quiz'      && renderQuiz()}
              {screen === 'summary'   && renderSummary()}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </InternalLayout>
  );
};

export default CustomRoom;

