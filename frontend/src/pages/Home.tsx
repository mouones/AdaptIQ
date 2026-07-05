/**
 * src/pages/Home.tsx
 *
 * Marketing/landing page composed of themed sections and CTA navigation.
 */

import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { 
  Library, 
  BookMarked, 
  ScrollText, 
  Compass, 
  ShieldQuestion, 
  Flame, 
  PenTool, 
  Search,
  BookOpen,
  ArrowRight,
  Sparkle,
  Trophy,
  Zap,
  BarChart3,
  StickyNote,
  Target,
  ChevronRight
} from 'lucide-react';

// Top navigation bar with scroll-aware styling and auth CTA buttons.
const Navbar = () => {
  const [isScrolled, setIsScrolled] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    const handleScroll = () => setIsScrolled(window.scrollY > 20);
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  return (
    <nav className={`fixed top-0 left-0 right-0 z-50 transition-all duration-500 ${isScrolled ? 'py-3 bg-[#F5F2E7]/95 backdrop-blur-md border-b border-[#D4AF37]/30 shadow-lg' : 'py-6 bg-transparent'}`}>
      <div className="max-w-7xl mx-auto px-8 flex items-center justify-between">
        <div className="flex items-center gap-3 cursor-pointer" onClick={() => navigate('/')}>
          <div className="w-12 h-12 bg-[#2D1B14] rounded-md flex items-center justify-center border border-[#D4AF37] rotate-3 hover:rotate-0 transition-transform shadow-md">
            <Library className="text-[#D4AF37] w-7 h-7" />
          </div>
          <div className="flex flex-col -gap-1">
            <span className="text-2xl font-black font-playfair tracking-tighter text-[#2D1B14]">Adapti<span className="text-[#D4AF37]">Q</span></span>
            <span className="text-[10px] uppercase tracking-[0.2em] font-bold opacity-60">The Digital Scriptorium</span>
          </div>
        </div>
        
        <div className="hidden lg:flex items-center gap-10 text-xs font-bold uppercase tracking-widest text-[#2D1B14]/70">
          <a href="#rituals" className="hover:text-[#D4AF37] transition-colors">The Paths</a>
          <a href="#archives" className="hover:text-[#D4AF37] transition-colors">The Archives</a>
          <a href="#journey" className="hover:text-[#D4AF37] transition-colors">The Journey</a>
        </div>

        <div className="flex items-center gap-4">
          <button onClick={() => navigate('/login')} className="px-5 py-2 text-xs font-bold uppercase tracking-widest text-[#2D1B14] hover:opacity-70 transition-opacity">
            Login
          </button>
          <button onClick={() => navigate('/signup')} className="px-6 py-3 rounded-md text-xs font-bold uppercase tracking-widest button-scholarly flex items-center gap-2 shadow-xl">
            Enroll Now <ArrowRight className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </nav>
  );
};

// Hero section introducing product positioning and primary CTA.
const Hero = () => {
  const navigate = useNavigate();
  
  return (
    <section className="relative pt-40 pb-20 md:pt-56 md:pb-40 overflow-hidden">
      <div className="absolute top-20 right-[-10%] w-[500px] h-[500px] opacity-[0.03] pointer-events-none rotate-12">
        <Compass className="w-full h-full text-[#2D1B14]" />
      </div>

      <div className="max-w-7xl mx-auto px-8 relative z-10 text-center">
        <div className="inline-block mb-8">
          <div className="flex items-center gap-3 px-5 py-2 border border-[#D4AF37]/40 rounded-full text-[11px] font-bold uppercase tracking-[0.4em] text-[#D4AF37]">
            <Sparkle className="w-3 h-3 fill-current" /> Wisdom Refined
          </div>
        </div>
        
        <h1 className="text-6xl md:text-8xl font-black font-playfair leading-[1.1] mb-8 text-[#2D1B14]">
          Cultivate <span className="italic">Profound</span> <br />
          <span className="gold-leaf font-black">Knowledge Mastery</span>
        </h1>
        
        <p className="text-xl md:text-2xl text-[#2D1B14]/70 max-w-2xl mx-auto mb-12 font-medium italic leading-relaxed">
          An adaptive scriptorium where ancient logic meets modern intelligence. Ground your learning in verified archives through dynamic cognitive rituals.
        </p>

        <div className="flex justify-center">
          <button onClick={() => navigate('/signup')} className="px-12 py-6 rounded-md button-scholarly font-bold text-sm uppercase tracking-[0.3em] shadow-2xl hover:scale-105 transition-transform group">
            Begin Your Pilgrimage <ChevronRight className="inline ml-2 w-4 h-4 group-hover:translate-x-1 transition-transform" />
          </button>
        </div>
      </div>
    </section>
  );
};

// Reusable card used across the three ritual pillars.
const RitualCard: React.FC<{ ritual: string; title: string; desc: string; icon: React.ReactNode }> = ({ ritual, title, desc, icon }) => (
  <div className="group h-full">
    <div className="relative h-full bg-[#FDFCF7] p-10 rounded-lg border border-[#2D1B14]/8 hover:border-[#D4AF37]/50 hover:-translate-y-1 transition-all duration-500 shadow-sm hover:shadow-xl flex flex-col">
      <div className="w-16 h-16 rounded-full border border-[#D4AF37]/30 flex items-center justify-center mb-8 text-[#2D1B14] group-hover:bg-[#2D1B14] group-hover:text-[#D4AF37] transition-all duration-500">
        {icon}
      </div>
      <div className="text-[10px] font-bold text-[#D4AF37] uppercase tracking-[0.3em] mb-3">{ritual} RITUAL</div>
      <h3 className="text-3xl font-black font-playfair text-[#2D1B14] mb-4">{title}</h3>
      <p className="text-[#2D1B14]/60 text-lg leading-relaxed mb-8 italic flex-grow">
        {desc}
      </p>
      <div className="w-12 h-0.5 bg-[#D4AF37]/30 group-hover:w-full transition-all duration-700"></div>
    </div>
  </div>
);

// Section describing the three adaptive learning paths.
const SectionRituals = () => {
  return (
    <section id="rituals" className="py-32 relative bg-[#F9F7F0]">
      <div className="max-w-7xl mx-auto px-8">
        <div className="text-center mb-24">
          <h2 className="text-5xl md:text-6xl font-black font-playfair text-[#2D1B14] mb-6">The Three Paths</h2>
          <div className="w-24 h-1 bg-[#D4AF37] mx-auto mb-8"></div>
          <p className="text-xl text-[#2D1B14]/70 italic max-w-2xl mx-auto">Each session adapts to your unique cognitive signature, ensuring your pursuit of knowledge never falters.</p>
        </div>

        <div className="grid md:grid-cols-3 gap-8">
          <RitualCard 
            ritual="Flow"
            title="Alchemist's Flow"
            desc="Adaptive MCQ balancing that maintains a 'Sweet Spot' of cognitive load, ensuring deep immersion without fatigue."
            icon={<Flame className="w-8 h-8" />}
          />
          <RitualCard 
            ritual="Retention"
            title="Scholar's Ritual"
            desc="Anchoring concepts through spaced repetition and AI-powered smart feedback sessions."
            icon={<BookMarked className="w-8 h-8" />}
          />
          <RitualCard 
            ritual="Challenge"
            title="Master's Trial"
            desc="Dynamic difficulty scaling that pushes analytical boundaries to their absolute limits."
            icon={<ShieldQuestion className="w-8 h-8" />}
          />
        </div>
      </div>
    </section>
  );
};

// Section highlighting grounding/RAG archival guarantees.
const ArchiveSection = () => {
  return (
    <section id="archives" className="py-32 px-8 overflow-hidden bg-white">
      <div className="max-w-7xl mx-auto">
        <div className="grid lg:grid-cols-2 gap-20 items-center">
          <div className="relative">
            <h2 className="text-5xl md:text-6xl font-black font-playfair text-[#2D1B14] mb-10 leading-tight">Grounded in <br /><span className="italic text-[#D4AF37]">Verified Archives</span></h2>
            <p className="text-xl text-[#2D1B14]/80 mb-10 leading-relaxed font-serif">
              Our intelligence is anchored to primary sources. Using RAG technology, every AI hint is verified against authorized scholarly archives, eliminating hallucinations.
            </p>
            
            <div className="grid sm:grid-cols-2 gap-6">
              {[
                { icon: <ScrollText />, title: 'RAG AI Hints', desc: 'Sourced from core texts.' },
                { icon: <Search />, title: 'Absolute Grounding', desc: 'No guessing, only archival truth.' }
              ].map((item, idx) => (
                <div key={idx} className="p-6 rounded-lg border border-[#D4AF37]/15 hover:border-[#D4AF37] transition-colors bg-[#FDFCF7] hover:shadow-md duration-300">
                  <div className="text-[#D4AF37] mb-4">{item.icon}</div>
                  <h4 className="text-lg font-bold font-playfair mb-1 text-[#2D1B14]">{item.title}</h4>
                  <p className="text-[#2D1B14]/60 italic text-xs leading-relaxed">{item.desc}</p>
                </div>
              ))}
            </div>
          </div>

          <div className="relative">
             <div className="relative leather-border bg-[#F5F2E7] rounded-lg p-8 md:p-12 shadow-2xl">
                <div className="flex justify-between items-center mb-8 pb-4 border-b border-[#D4AF37]/30">
                  <div className="text-xs uppercase font-bold tracking-[0.2em] text-[#D4AF37]">Archival Extraction</div>
                  <BookOpen className="w-5 h-5 opacity-40" />
                </div>
                
                <div className="space-y-6">
                  <div className="text-lg font-serif italic text-[#2D1B14]/80 leading-relaxed">
                    "Question: Analyze the role of heuristics in adaptive problem-solving systems..."
                  </div>
                  
                  <div className="p-6 bg-white rounded-r-lg rounded-l-sm border-l-4 border-[#2D1B14] italic text-sm text-[#2D1B14]/70 font-serif">
                    <span className="font-bold block mb-2 text-[#D4AF37] uppercase text-[10px] tracking-widest">Archive Ref: CogSci Vol 14.</span>
                    "Heuristics act as cognitive shortcuts that allow for efficient decision-making under high-entropy conditions..."
                  </div>

                  <div className="pt-4 flex items-center gap-3">
                    <div className="w-8 h-8 rounded-full bg-[#2D1B14] flex items-center justify-center">
                      <PenTool className="w-4 h-4 text-[#D4AF37]" />
                    </div>
                    <span className="text-[10px] font-bold uppercase tracking-widest opacity-60 italic">Generating Grounded Feedback...</span>
                  </div>
                </div>
             </div>
          </div>
        </div>
      </div>
    </section>
  );
};

// Feature matrix summarizing the scholar journey capabilities.
const FeatureGrid = () => {
  const features = [
    { icon: <Trophy />, title: 'Ranking Tiers', desc: 'Ascend from Novice to Grandmaster through scholarly rigor.' },
    { icon: <Zap />, title: 'Streak System', desc: 'Maintain your Daily Illumination streak to unlock archival access.' },
    { icon: <BarChart3 />, title: 'Progress Dashboard', desc: 'Visualize your cognitive growth through intricate scrolls of data.' },
    { icon: <StickyNote />, title: 'Personalized Grimoire', desc: 'Notes automatically linked to your mistakes for targeted review.' },
    { icon: <Target />, title: 'Weak Area Summary', desc: 'AI-generated summaries of your conceptual blind spots.' },
    { icon: <Sparkle />, title: 'Smart Feedback', desc: 'Context-aware corrections that teach rather than just correct.' }
  ];

  return (
    <section id="journey" className="py-32 px-8 bg-[#2D1B14]">
      <div className="max-w-7xl mx-auto">
        <div className="text-center mb-24">
          <h2 className="text-5xl md:text-6xl font-black font-playfair text-[#F5F2E7] mb-6 gold-leaf">The Scholar's Journey</h2>
          <p className="text-xl text-[#F5F2E7]/60 italic max-w-2xl mx-auto">Engagement refined. Every interaction contributes to your permanent intellectual legacy.</p>
        </div>

        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-10">
          {features.map((f, i) => (
            <div key={i} className="p-8 rounded-lg border border-[#F5F2E7]/8 bg-white/5 hover:bg-white/10 hover:-translate-y-0.5 transition-all duration-300 group">
              <div className="w-12 h-12 rounded-md flex items-center justify-center text-[#D4AF37] mb-6 border border-[#D4AF37]/30 group-hover:bg-[#D4AF37] group-hover:text-[#2D1B14] transition-all">
                {f.icon}
              </div>
              <h4 className="text-xl font-bold font-playfair text-[#F5F2E7] mb-3">{f.title}</h4>
              <p className="text-[#F5F2E7]/50 text-sm leading-relaxed italic">{f.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
};

// Footer with brand/legal/navigation links.
const Footer = () => {
  return (
    <footer className="py-20 px-8 border-t border-[#D4AF37]/20 bg-[#F9F7F0]">
      <div className="max-w-7xl mx-auto flex flex-col md:flex-row justify-between items-center gap-12">
        <div className="flex flex-col items-center md:items-start gap-4">
          <div className="flex items-center gap-3">
            <Library className="text-[#2D1B14] w-7 h-7" />
            <span className="text-2xl font-black font-playfair text-[#2D1B14]">AdaptiQ</span>
          </div>
          <p className="text-[10px] uppercase font-bold tracking-[0.3em] opacity-40 italic">Vires Acquirit Eundo — Strength Through Knowledge</p>
        </div>
        
        <div className="flex gap-12 text-xs font-bold uppercase tracking-widest text-[#2D1B14]/60">
          <a href="#" className="hover:text-[#D4AF37] transition-colors">The Archives</a>
          <a href="#" className="hover:text-[#D4AF37] transition-colors">Methodology</a>
          <a href="#" className="hover:text-[#D4AF37] transition-colors">Privacy</a>
        </div>
      </div>
    </footer>
  );
};

// Compose full landing page from navbar, sections, and footer.
export default function Home() {
  return (
    <div className="min-h-screen selection:bg-[#D4AF37] selection:text-[#2D1B14]">
      <Navbar />
      <Hero />
      <SectionRituals />
      <ArchiveSection />
      <FeatureGrid />
      <Footer />
    </div>
  );
}
