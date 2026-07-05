/** Shared UI component for OnboardingModal behavior. */

import React, { useState } from 'react';
import { Check, Info, X, Sparkles } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';

interface OnboardingModalProps {
  onComplete: (confidentTopics: string[], learnTopics: string[]) => void;
  onSkip: () => void;
}

const TOPICS = [
  'World War II',
  'Cold War',
  'Ancient Rome',
  'Geography – France',
  'Geography – Japan',
  'The Renaissance',
  'Ancient Egypt',
  'Space Exploration'
];

const OnboardingModal: React.FC<OnboardingModalProps> = ({ onComplete, onSkip }) => {
  const [step, setStep] = useState(1);
  const [confidentTopics, setConfidentTopics] = useState<string[]>([]);
  const [learnTopics, setLearnTopics] = useState<string[]>([]);
  const [showConfirmation, setShowConfirmation] = useState(false);

  const toggleTopic = (topic: string, list: string[], setList: React.Dispatch<React.SetStateAction<string[]>>) => {
    if (list.includes(topic)) {
      setList(list.filter(t => t !== topic));
    } else if (list.length < 3) {
      setList([...list, topic]);
    }
  };

  const handleContinue = () => {
    if (step === 1) {
      setStep(2);
    } else if (step === 2) {
      setShowConfirmation(true);
      setTimeout(() => {
        onComplete(confidentTopics, learnTopics);
      }, 3000);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <motion.div 
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="absolute inset-0 bg-[#2D1B14]/70 backdrop-blur-md"
        onClick={onSkip}
      />

      {/* Modal */}
      <motion.div 
        initial={{ scale: 0.9, opacity: 0, y: 20 }}
        animate={{ scale: 1, opacity: 1, y: 0 }}
        exit={{ scale: 0.9, opacity: 0, y: 20 }}
        className="relative bg-[#FDFCF7] w-full max-w-2xl rounded-sm shadow-2xl border border-[#D4AF37]/30 overflow-hidden"
      >
        {!showConfirmation ? (
          <div className="p-8 md:p-12">
            <button 
              onClick={onSkip}
              className="absolute top-6 right-6 text-[#2D1B14]/40 hover:text-[#2D1B14] transition-colors"
            >
              <X className="w-6 h-6" />
            </button>

            <div className="mb-8">
              <div className="flex items-center gap-3 mb-4">
                <div className="w-10 h-10 bg-[#D4AF37] rounded-sm flex items-center justify-center rotate-3 shadow-lg">
                  <Sparkles className="text-[#2D1B14] w-6 h-6" />
                </div>
                <h2 className="text-3xl font-black font-playfair text-[#2D1B14]">
                  Welcome! Let's get to know you.
                </h2>
              </div>
              <p className="text-[#2D1B14]/60 italic font-serif">
                Tell us about your interests so we can tailor your experience in the archives.
              </p>
            </div>

            <div className="space-y-8">
              {step === 1 ? (
                <motion.div 
                  initial={{ x: 20, opacity: 0 }}
                  animate={{ x: 0, opacity: 1 }}
                  className="space-y-6"
                >
                  <div className="flex items-center gap-2 text-[#D4AF37]">
                    <Info className="w-4 h-4" />
                    <span className="text-[10px] font-bold uppercase tracking-widest">Step 1 of 2</span>
                  </div>
                  <h3 className="text-xl font-black font-playfair text-[#2D1B14]">
                    What topics do you feel confident about?
                  </h3>
                  <p className="text-xs text-[#2D1B14]/40 uppercase tracking-widest font-bold">Select up to 3</p>
                  
                  <div className="grid grid-cols-2 gap-3">
                    {TOPICS.map(topic => (
                      <button
                        key={topic}
                        onClick={() => toggleTopic(topic, confidentTopics, setConfidentTopics)}
                        className={`p-4 text-left text-xs font-bold uppercase tracking-widest border transition-all ${
                          confidentTopics.includes(topic)
                            ? 'bg-[#2D1B14] text-[#F5F2E7] border-[#2D1B14]'
                            : 'bg-white text-[#2D1B14]/60 border-[#2D1B14]/10 hover:border-[#D4AF37]/50'
                        }`}
                      >
                        <div className="flex justify-between items-center">
                          {topic}
                          {confidentTopics.includes(topic) && <Check className="w-4 h-4" />}
                        </div>
                      </button>
                    ))}
                  </div>
                </motion.div>
              ) : (
                <motion.div 
                  initial={{ x: 20, opacity: 0 }}
                  animate={{ x: 0, opacity: 1 }}
                  className="space-y-6"
                >
                  <div className="flex items-center gap-2 text-[#D4AF37]">
                    <Info className="w-4 h-4" />
                    <span className="text-[10px] font-bold uppercase tracking-widest">Step 2 of 2</span>
                  </div>
                  <h3 className="text-xl font-black font-playfair text-[#2D1B14]">
                    What do you want to learn more about?
                  </h3>
                  <p className="text-xs text-[#2D1B14]/40 uppercase tracking-widest font-bold">Select up to 3</p>
                  
                  <div className="grid grid-cols-2 gap-3">
                    {TOPICS.map(topic => (
                      <button
                        key={topic}
                        onClick={() => toggleTopic(topic, learnTopics, setLearnTopics)}
                        className={`p-4 text-left text-xs font-bold uppercase tracking-widest border transition-all ${
                          learnTopics.includes(topic)
                            ? 'bg-[#2D1B14] text-[#F5F2E7] border-[#2D1B14]'
                            : 'bg-white text-[#2D1B14]/60 border-[#2D1B14]/10 hover:border-[#D4AF37]/50'
                        }`}
                      >
                        <div className="flex justify-between items-center">
                          {topic}
                          {learnTopics.includes(topic) && <Check className="w-4 h-4" />}
                        </div>
                      </button>
                    ))}
                  </div>
                </motion.div>
              )}

              <div className="flex items-center justify-between pt-8 border-t border-[#2D1B14]/10">
                <button 
                  onClick={onSkip}
                  className="text-[10px] font-bold uppercase tracking-[0.2em] text-[#2D1B14]/40 hover:text-[#2D1B14] transition-colors"
                >
                  Skip Onboarding
                </button>
                <div className="flex gap-4">
                  {step === 2 && (
                    <button 
                      onClick={() => setStep(1)}
                      className="px-8 py-3 text-[10px] font-bold uppercase tracking-[0.2em] border border-[#2D1B14]/10 hover:bg-[#2D1B14]/5 transition-all"
                    >
                      Back
                    </button>
                  )}
                  <button 
                    onClick={handleContinue}
                    disabled={step === 1 ? confidentTopics.length === 0 : learnTopics.length === 0}
                    className="px-12 py-3 bg-[#2D1B14] text-[#F5F2E7] text-[10px] font-bold uppercase tracking-[0.2em] hover:bg-[#3d261c] transition-all disabled:opacity-50 disabled:cursor-not-allowed shadow-lg"
                  >
                    {step === 1 ? 'Next' : 'I\'m ready to start learning'}
                  </button>
                </div>
              </div>
            </div>
          </div>
        ) : (
          <motion.div 
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="p-12 text-center space-y-6 bg-[#2D1B14] text-[#F5F2E7]"
          >
            <div className="w-20 h-20 bg-[#D4AF37] rounded-full flex items-center justify-center mx-auto mb-8 rotate-12 shadow-2xl">
              <Sparkles className="text-[#2D1B14] w-10 h-10" />
            </div>
            <h2 className="text-4xl font-black font-playfair">Excellent Choices!</h2>
            <p className="text-[#F5F2E7]/60 italic font-serif text-lg">
              You selected: <span className="text-[#D4AF37]">{[...confidentTopics, ...learnTopics].slice(0, 3).join(', ')}...</span>
            </p>
            <p className="text-[#D4AF37] text-sm font-bold uppercase tracking-widest">
              You'll see these in Custom Room!
            </p>
            <div className="pt-8">
              <div className="w-12 h-1 bg-[#D4AF37]/30 mx-auto rounded-full overflow-hidden">
                <motion.div 
                  initial={{ width: 0 }}
                  animate={{ width: '100%' }}
                  transition={{ duration: 2.5, ease: "linear" }}
                  className="h-full bg-[#D4AF37]"
                />
              </div>
            </div>
          </motion.div>
        )}
      </motion.div>
    </div>
  );
};

export default OnboardingModal;
