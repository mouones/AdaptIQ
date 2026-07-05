/** Shared UI component for GuidedTour behavior. */

import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { ArrowRight, X } from 'lucide-react';

interface TourStep {
  targetId: string;
  title: string;
  content: string;
  position: 'top' | 'bottom' | 'left' | 'right';
}

interface GuidedTourProps {
  onComplete: () => void;
}

const TOUR_STEPS: TourStep[] = [
  {
    targetId: 'sidebar-room-classic',
    title: 'Classic Room',
    content: 'Start practicing here. Classic Room is your main practice area.',
    position: 'right'
  },
  {
    targetId: 'sidebar-room-custom',
    title: 'Custom Room',
    content: 'Study specific topics you care about, like World War II or France.',
    position: 'right'
  },
  {
    targetId: 'sidebar-room-pvp',
    title: 'PvP Room',
    content: 'Play 1v1 quiz battles against other users of the same rank.',
    position: 'right'
  },
  {
    targetId: 'sidebar-room-visual',
    title: 'Visual Room',
    content: 'Answer quiz questions based on the image shown to you. Great for practicing visual recognition and context clues.',
    position: 'right'
  },
  {
    targetId: 'sidebar-room-challenge',
    title: 'Challenge Room',
    content: 'Test your rank in a challenging 5-level mode.',
    position: 'right'
  }
];

const GuidedTour: React.FC<GuidedTourProps> = ({ onComplete }) => {
  const [currentStep, setCurrentStep] = useState(0);
  const [coords, setCoords] = useState({ top: 0, left: 0, width: 0, height: 0 });
  const [isReady, setIsReady] = useState(false);

  // High-frequency coordinate tracking for perfect accuracy during animations
  useEffect(() => {
    let animationFrameId: number;
    
    const update = () => {
      const target = document.getElementById(TOUR_STEPS[currentStep].targetId);
      if (target) {
        const rect = target.getBoundingClientRect();
        setCoords({
          top: rect.top,
          left: rect.left,
          width: rect.width,
          height: rect.height
        });
        if (!isReady) setIsReady(true);
      }
      animationFrameId = requestAnimationFrame(update);
    };

    update();
    return () => cancelAnimationFrame(animationFrameId);
  }, [currentStep, isReady]);

  useEffect(() => {
    // Advance on any click
    const handleGlobalClick = (e: MouseEvent) => {
      // Don't advance if clicking the skip button or the "Got it" button (which has its own handler)
      const target = e.target as HTMLElement;
      if (target.closest('.skip-tour-btn') || target.closest('.next-step-btn')) return;
      
      if (currentStep < TOUR_STEPS.length - 1) {
        setCurrentStep(prev => prev + 1);
      } else {
        onComplete();
      }
    };

    window.addEventListener('mousedown', handleGlobalClick);

    return () => {
      window.removeEventListener('mousedown', handleGlobalClick);
    };
  }, [currentStep, onComplete]);

  const handleNext = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (currentStep < TOUR_STEPS.length - 1) {
      setCurrentStep(currentStep + 1);
    } else {
      onComplete();
    }
  };

  const step = TOUR_STEPS[currentStep];

  return (
    <div className="fixed inset-0 z-[60] pointer-events-none">
      {/* Spotlight Overlay using SVG Mask */}
      <svg className="absolute inset-0 w-full h-full">
        <defs>
          <mask id="spotlight-mask">
            <rect x="0" y="0" width="100%" height="100%" fill="white" />
            <motion.rect
              animate={{
                x: coords.left - 8,
                y: coords.top - 4,
                width: coords.width + 16,
                height: coords.height + 8,
              }}
              transition={{ type: 'spring', damping: 25, stiffness: 200 }}
              rx="4"
              fill="black"
            />
          </mask>
        </defs>
        <rect
          x="0"
          y="0"
          width="100%"
          height="100%"
          fill="rgba(0, 0, 0, 0.7)"
          mask="url(#spotlight-mask)"
          className="backdrop-blur-[2px]"
        />
      </svg>

      {/* Target Highlight Pulse */}
      <AnimatePresence>
        {isReady && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ 
              opacity: 1,
              top: coords.top - 4,
              left: coords.left - 8,
              width: coords.width + 16,
              height: coords.height + 8,
            }}
            transition={{ type: 'spring', damping: 25, stiffness: 200 }}
            className="absolute border-2 border-[#D4AF37] rounded-sm z-[65]"
          >
            <motion.div 
              animate={{ scale: [1, 1.1, 1], opacity: [0.5, 0, 0.5] }}
              transition={{ duration: 2, repeat: Infinity }}
              className="absolute inset-0 border-4 border-[#D4AF37] rounded-sm"
            />
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence mode="wait">
        {isReady && (
          <motion.div
            key={currentStep}
            initial={{ opacity: 0, scale: 0.95, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 20 }}
            style={{
              position: 'absolute',
              top: coords.top + coords.height / 2,
              left: coords.left + coords.width + 48, 
              transform: 'translateY(-50%)',
            }}
            className="pointer-events-auto w-[400px] bg-[#2D1B14] text-[#F5F2E7] p-10 rounded-sm shadow-[0_30px_100px_rgba(0,0,0,0.9)] z-[70] border border-[#D4AF37]/30"
          >
            <div className="flex justify-between items-start mb-6">
              <h4 className="text-[10px] font-bold uppercase tracking-[0.3em] text-[#D4AF37]">
                {step.title}
              </h4>
              <button 
                onClick={(e) => { e.stopPropagation(); onComplete(); }}
                className="text-[#F5F2E7]/40 hover:text-[#F5F2E7] transition-colors -mt-2 -mr-2 p-2"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <p className="text-sm font-serif italic mb-8 leading-relaxed text-[#F5F2E7]/80">
              {step.content}
            </p>

            <div className="flex items-center justify-between">
              {/* Progress Dots */}
              <div className="flex gap-2">
                {TOUR_STEPS.map((_, idx) => (
                  <div 
                    key={idx}
                    className={`h-1 rounded-full transition-all duration-500 ${
                      idx === currentStep ? 'bg-[#D4AF37] w-6' : 'bg-[#D4AF37]/20 w-2'
                    }`}
                  />
                ))}
              </div>

              <button
                onClick={handleNext}
                className="next-step-btn flex items-center gap-2 bg-[#D4AF37] text-[#2D1B14] px-6 py-2.5 text-[10px] font-bold uppercase tracking-[0.2em] hover:bg-[#b8962f] transition-all shadow-lg active:scale-95"
              >
                {currentStep === TOUR_STEPS.length - 1 ? 'Finish' : 'Got it'}
                <ArrowRight className="w-3 h-3" />
              </button>
            </div>
            
            <div className="mt-6 pt-4 border-t border-[#D4AF37]/10 text-[8px] font-bold uppercase tracking-widest text-[#F5F2E7]/20 text-center">
              Click anywhere to continue
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Skip Button */}
      <button
        onClick={onComplete}
        className="skip-tour-btn fixed bottom-8 right-8 pointer-events-auto bg-[#2D1B14] border border-[#D4AF37]/30 text-[#F5F2E7] px-8 py-4 text-[10px] font-bold uppercase tracking-[0.3em] hover:bg-[#3d261c] hover:border-[#D4AF37] transition-all rounded-sm shadow-2xl"
      >
        Skip Tour
      </button>
    </div>
  );
};

export default GuidedTour;
