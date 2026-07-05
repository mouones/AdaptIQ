/** Shared UI component for ChatAssistant behavior. */

import React, { useState, useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import { motion, AnimatePresence } from 'motion/react';
import { 
  Trash2, 
  Copy, 
  Check, 
  Compass, 
  Send, 
  ChevronDown, 
  AlertTriangle, 
  Loader2 
} from 'lucide-react';
import { ChatMessage } from '../types/chat';
import { askScholar } from '../services/scholarService';
import { useAuth } from '../context/AuthContext';

const BookSparkleIcon: React.FC = () => (
  <svg className="w-6 h-6 text-[#D4AF37]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
    <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
    <path d="M12 2l0.8 1.8 1.8 0.8-1.8 0.8-0.8 1.8-0.8-1.8-1.8-0.8 1.8-0.8z" fill="currentColor" stroke="none" />
  </svg>
);

const SmallSparkleIcon: React.FC<{ className?: string }> = ({ className = "w-4 h-4" }) => (
  <svg className={`${className} text-[#D4AF37]`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M12 2l1.2 2.8 2.8 1.2-2.8 1.2-1.2 2.8-1.2-2.8-2.8-1.2 2.8-1.2z" fill="currentColor" stroke="none" />
  </svg>
);

const ChatAssistant: React.FC = () => {
  const location = useLocation();
  const { user } = useAuth();
  const [isOpen, setIsOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  
  // Hover & Tooltip State
  const [showTooltip, setShowTooltip] = useState(false);
  const hoverTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  
  // Rate limits
  const [lastSentTime, setLastSentTime] = useState(0);
  const [isShaking, setIsShaking] = useState(false);
  const [placeholder, setPlaceholder] = useState('Ask about history or geography...');
  
  // Clear conversation status toast
  const [showClearToast, setShowClearToast] = useState(false);

  // Streaming State & Cursor Control
  const [streamingId, setStreamingId] = useState<string | null>(null);
  const [visibleCharsCounts, setVisibleCharsCounts] = useState<Record<string, number>>({});
  
  // Unread badge counter
  const [unreadCount, setUnreadCount] = useState(0);
  const hasOpenedOnceRef = useRef(false);

  // RAG Source Cycling Animation
  const [phraseIndex, setPhraseIndex] = useState(0);
  const phrases = [
    "Consulting Wikipedia archives...",
    "Retrieving verified sources...",  
    "Cross-referencing Wikidata...",
    "Synthesizing scholarly context..."
  ];

  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // 1. Position & Visibility Guard
  const isRoomActive = [
    '/rooms/classic',
    '/rooms/challenge',
    '/rooms/custom',
    '/rooms/pvp',
    '/rooms/visual'
  ].some(path => location.pathname.startsWith(path));

  // 2. Keep chat history in memory only.
  useEffect(() => {
    setMessages([]);
    setVisibleCharsCounts({});
    setStreamingId(null);
    setUnreadCount(0);
  }, [location.pathname, user?.id]);

  // Handle open state transition (reset unread count)
  useEffect(() => {
    if (isOpen) {
      setUnreadCount(0);
      hasOpenedOnceRef.current = true;
      // Focus textarea
      setTimeout(() => {
        textareaRef.current?.focus();
      }, 200);
    }
  }, [isOpen]);

  // 3. Auto Scroll on New Message
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingId, visibleCharsCounts, isLoading]);

  // 4. Source cyclying effect
  useEffect(() => {
    if (!isLoading) return;
    const interval = setInterval(() => {
      setPhraseIndex(prev => (prev + 1) % phrases.length);
    }, 1200);
    return () => clearInterval(interval);
  }, [isLoading]);

  // 5. Typewriter character stream engine
  useEffect(() => {
    if (!streamingId) return;
    const msg = messages.find(m => m.id === streamingId);
    if (!msg) {
      setStreamingId(null);
      return;
    }

    const totalLength = msg.text.length;
    let index = visibleCharsCounts[streamingId] || 0;

    const timer = setInterval(() => {
      index += 1;
      setVisibleCharsCounts(prev => ({
        ...prev,
        [streamingId]: index
      }));

      if (index >= totalLength) {
        clearInterval(timer);
        setStreamingId(null);
      }
    }, 12);

    return () => clearInterval(timer);
  }, [streamingId, messages]);

  // 6. Keyboard Shortcut Toggle (Alt+S)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const isMac = navigator.platform.toUpperCase().indexOf('MAC') >= 0;
      const triggerKey = isMac ? e.altKey : e.altKey;
      if (triggerKey && e.key.toLowerCase() === 's') {
        e.preventDefault();
        setIsOpen(prev => !prev);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  if (!user || isRoomActive) return null;

  // Hover Tooltip Trigger Helpers
  const handleMouseEnter = () => {
    hoverTimeoutRef.current = setTimeout(() => {
      setShowTooltip(true);
    }, 600);
  };

  const handleMouseLeave = () => {
    if (hoverTimeoutRef.current) {
      clearTimeout(hoverTimeoutRef.current);
    }
    setShowTooltip(false);
  };

  const clearChat = () => {
    setMessages([]);
    setStreamingId(null);
    setVisibleCharsCounts({});
    setShowClearToast(true);
    setTimeout(() => {
      setShowClearToast(false);
    }, 2000);
  };

  const handleSendMessage = async (textToSend: string) => {
    const trimmed = textToSend.trim();
    if (!trimmed) return;

    // Rate Limit Check
    const now = Date.now();
    if (now - lastSentTime < 1500) {
      setIsShaking(true);
      setPlaceholder('Please wait...');
      setTimeout(() => {
        setIsShaking(false);
      }, 300);
      setTimeout(() => {
        setPlaceholder('Ask about history or geography...');
      }, 1000);
      return;
    }

    setLastSentTime(now);
    setInput('');
    setIsLoading(true);

    const userMsg: ChatMessage = {
      id: `m_${Math.random().toString(36).substr(2, 9)}`,
      sender: 'user',
      text: trimmed,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    };

    // Prepend context dynamically if dashboard or coming from a session
    let contextMeta = "";
    if (location.pathname === '/dashboard') {
      contextMeta = "[Archival context: User is currently reading the dashboard] ";
    }
    const lastRoom = sessionStorage.getItem("adaptiq_last_room");
    if (lastRoom) {
      contextMeta += `[Context: User recently completed a session in the ${lastRoom}] `;
    }

    const currentTopicHint = trimmed.toLowerCase().includes('map') || trimmed.toLowerCase().includes('where') || trimmed.toLowerCase().includes('country') ? 'geography' : 'history';

    const currentList = [...messages, userMsg];
    setMessages(currentList);

    try {
      const result = await askScholar(contextMeta + trimmed, currentTopicHint);
      
      const assistantMsg: ChatMessage = {
        id: `m_${Math.random().toString(36).substr(2, 9)}`,
        sender: 'assistant',
        text: result.answer,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        sources: result.sources,
        topic: result.topic
      };

      setMessages(prev => [...prev, assistantMsg]);
      setVisibleCharsCounts(prev => ({ ...prev, [assistantMsg.id]: 0 }));
      setStreamingId(assistantMsg.id);

      // Increment Unread if closed
      if (!isOpen) {
        setUnreadCount(prev => prev + 1);
      }

    } catch (err: any) {
      console.error(err);
      let errorText = "An error occurred in deep archives. Please re-authenticate.";
      if (err.message === "OUT_OF_SCOPE") {
        errorText = "I can only assist with history and geography topics.";
      } else if (err.message === "SERVICE_UNAVAILABLE" || err.message === "BAD_REQUEST") {
        errorText = "Sources temporarily unavailable, please retry.";
      } else if (err.message === "TIMEOUT") {
        errorText = "Connection timed out. Chronology archives taking too long to respond.";
      }

      const assistantMsgErr: ChatMessage = {
        id: `m_${Math.random().toString(36).substr(2, 9)}`,
        sender: 'assistant',
        text: errorText,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        isError: true
      };

      setMessages(prev => [...prev, assistantMsgErr]);
      setVisibleCharsCounts(prev => ({ ...prev, [assistantMsgErr.id]: 0 }));
      setStreamingId(assistantMsgErr.id);

      if (!isOpen) {
        setUnreadCount(prev => prev + 1);
      }
    } finally {
      setIsLoading(false);
    }
  };

  const handleCopyText = (msgId: string, text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(msgId);
    setTimeout(() => {
      setCopiedId(null);
    }, 1500);
  };

  // Auto-resize search input
  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    e.target.style.height = 'auto';
    e.target.style.height = `${Math.min(e.target.scrollHeight, 100)}px`;
  };

  const handleKeyDownInput = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage(input);
    }
  };

  const lastRoomName = sessionStorage.getItem("adaptiq_last_room");

  return (
    <div className="fixed bottom-6 right-6 z-[9999] pointer-events-none flex flex-col items-end">
      {/* Dynamic Keyframes injected into DOM safely */}
      <style>{`
        @keyframes readyPulse {
          0% { transform: scale(1); opacity: 1; }
          50% { transform: scale(1.4); opacity: 0.4; }
          100% { transform: scale(1); opacity: 1; }
        }
        @keyframes cursorBlink {
          0%, 100% { opacity: 0; }
          50% { opacity: 1; }
        }
        @keyframes limitShake {
          0%, 100% { transform: translateX(0); }
          20%, 60% { transform: translateX(-4px); }
          40%, 80% { transform: translateX(4px); }
        }
        .animate-ready-pulse {
          animation: readyPulse 2.5s infinite;
        }
        .animate-cursor-blink {
          animation: cursorBlink 0.8s step-end infinite;
        }
        .animate-limit-shake {
          animation: limitShake 0.3s ease-in-out;
        }
      `}</style>

      {/* CHAT PANEL */}
      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ scale: 0.85, opacity: 0, y: 20 }}
            animate={{ scale: 1, opacity: 1, y: 0 }}
            exit={{ scale: 0.85, opacity: 0, y: 20 }}
            transition={{ type: 'spring', damping: 20, stiffness: 220 }}
            style={{ originX: 1, originY: 1 }}
            className="pointer-events-auto w-[380px] h-[520px] max-w-[calc(100vw-32px)] sm:max-w-[380px] max-h-[70vh] sm:max-h-[520px] bg-[#F5F2E7] text-[#2D1B14] rounded-sm shadow-[0_30px_90px_rgba(45,27,20,0.35)] border border-[#D4AF37]/35 flex flex-col overflow-hidden mb-4"
          >
            {/* PANEL HEADER */}
            <header className="h-14 bg-[#2D1B14] text-[#F5F2E7] px-4 flex items-center justify-between border-b border-[#D4AF37]/25 shrink-0">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-sm bg-[#D4AF37]/10 flex items-center justify-center border border-[#D4AF37]/30">
                  <BookSparkleIcon />
                </div>
                <div>
                  <h3 className="text-sm font-black font-playfair tracking-tight text-[#F5F2E7]">
                    The Scholar
                  </h3>
                  <p className="text-[9px] font-bold uppercase tracking-[0.2em] text-[#F5F2E7]/50 leading-none mt-0.5">
                    Ask about history & geography
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                {messages.length > 0 && (
                  <button 
                    onClick={clearChat}
                    title="Clear Chat History"
                    className="p-1.5 hover:bg-white/5 text-[#F5F2E7]/40 hover:text-red-400 rounded-sm transition-colors cursor-pointer"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                )}
                <button 
                  onClick={() => setIsOpen(false)}
                  className="p-1.5 hover:bg-white/5 text-[#F5F2E7]/40 hover:text-[#D4AF37] rounded-sm transition-colors cursor-pointer"
                >
                  <ChevronDown className="w-4 h-4" />
                </button>
              </div>
            </header>

            {/* STATUS DIALOG (CLEAR CONVERSATION) */}
            <AnimatePresence>
              {showClearToast && (
                <motion.div 
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  className="absolute inset-0 z-40 bg-[#FBFAF2]/95 backdrop-blur-sm flex items-center justify-center text-center"
                >
                  <div className="space-y-2 p-6">
                    <Trash2 className="w-8 h-8 mx-auto text-red-500 animate-bounce" />
                    <p className="text-sm font-black font-playfair">History Cleared</p>
                    <p className="text-xs text-[#2D1B14]/60 italic font-serif">The archives of this conversation have been returned to silence.</p>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* SOURCE FETCH INDICATOR */}
            <AnimatePresence>
              {isLoading && (
                <motion.div 
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 32, opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  className="bg-[#FBFAF2] border-b border-[#D4AF37]/15 flex items-center px-4 overflow-hidden shrink-0 border-l-2 border-[#D4AF37] relative"
                >
                  <div className="flex items-center gap-2">
                    <Loader2 className="w-3.5 h-3.5 text-[#D4AF37]/80 animate-spin" />
                    <span className="text-[10px] italic font-serif text-[#2D1B14]/80">
                      {phrases[phraseIndex]}
                    </span>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* MESSAGE CONTAINER */}
            <div className="flex-grow overflow-y-auto p-4 space-y-4 flex flex-col relative messages-area scroll-smooth">
              {messages.length === 0 ? (
                /* EMPTY STATE */
                <div className="flex-grow flex flex-col items-center justify-center text-center px-6 py-8">
                  <div className="relative mb-4 flex items-center justify-center">
                    <BookSparkleIcon />
                  </div>
                  <p className="text-xs font-bold uppercase tracking-[0.2em] text-[#D4AF37] mb-2">Encyclopedic Archive</p>
                  <p className="text-sm font-serif italic text-[#2D1B14]/60 max-w-xs mb-6">
                    {lastRoomName 
                      ? "Continue exploring topics from your session." 
                      : "Ask about any historical event or geographical topic."}
                  </p>
                  
                  <div className="w-full space-y-2 max-w-[280px]">
                    {[
                      "What caused WWI?",
                      "Tell me about ancient Egypt",
                      "Where is the Sahara?"
                    ].map((chip) => (
                      <button
                        key={chip}
                        onClick={() => handleSendMessage(chip)}
                        className="w-full py-2.5 px-4 rounded-sm border border-[#D4AF37]/35 bg-[#FBFAF2] text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/80 hover:bg-[#2D1B14] hover:text-[#F5F2E7] hover:border-transparent transition-all truncate text-left flex items-center gap-2 shadow-sm cursor-pointer"
                      >
                        <SmallSparkleIcon className="w-3 h-3 shrink-0" />
                        <span>{chip}</span>
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                /* CHAT HISTORY */
                messages.map((msg) => {
                  const isUser = msg.sender === 'user';
                  const isCurrentStreaming = streamingId === msg.id;
                  const charLimit = visibleCharsCounts[msg.id];
                  const textToShow = isUser 
                    ? msg.text 
                    : (charLimit !== undefined ? msg.text.slice(0, charLimit) : msg.text);
                  const isFullyRendered = !isUser && !isCurrentStreaming;

                  return (
                    <div 
                      key={msg.id} 
                      className={`flex flex-col ${isUser ? 'items-end' : 'items-start'} group relative`}
                    >
                      {/* Message Bubble wrapper */}
                      <div className="relative max-w-[85%]">
                        <div 
                          className={`p-3.5 text-sm leading-relaxed rounded-sm ${
                            isUser 
                              ? 'bg-[#2D1B14] text-[#F5F2E7] font-serif rounded-tr-none rounded-l-md rounded-br-md shadow-sm' 
                              : msg.isError
                                ? 'bg-[#FCF5F5] text-red-800 border-l-4 border-red-500 rounded-tl-none rounded-r-md rounded-bl-md shadow-sm border border-[#D4AF37]/10'
                                : 'bg-white text-[#2D1B14] border border-[#2D1B14]/8 px-4 rounded-tl-none rounded-r-md rounded-bl-md border-l-4 border-l-[#D4AF37] shadow-sm font-serif'
                          }`}
                        >
                          {/* Triangle icon for error bubble */}
                          {!isUser && msg.isError && (
                            <AlertTriangle className="w-4 h-4 text-red-500 inline-block mr-2 align-text-bottom shrink-0" />
                          )}

                          {/* Render streamed characters dynamically */}
                          <span className="whitespace-pre-wrap">{textToShow}</span>
                          
                          {/* Cursor rendering */}
                          {isCurrentStreaming && (
                            <span className="inline-block ml-0.5 font-bold text-[#D4AF37] animate-cursor-blink">│</span>
                          )}
                        </div>

                        {/* Copy Code button */}
                        {isFullyRendered && !msg.isError && (
                          <button
                            onClick={() => handleCopyText(msg.id, msg.text)}
                            className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 p-1 bg-white/80 hover:bg-white rounded-sm border border-[#2D1B14]/10 transition-all text-[#2D1B14]/50 hover:text-[#2D1B14] cursor-pointer shadow-sm"
                            title="Copy response"
                          >
                            {copiedId === msg.id ? (
                              <Check className="w-3.5 h-3.5 text-green-600" />
                            ) : (
                              <Copy className="w-3.5 h-3.5" />
                            )}
                          </button>
                        )}
                      </div>

                      {/* TIMESTAMP & DATA METADATA */}
                      <div className={`text-[9px] font-bold uppercase tracking-widest text-[#2D1B14]/40 mt-1.5 flex items-center gap-2 px-1`}>
                        <span>{msg.timestamp}</span>
                        {isFullyRendered && msg.topic && (
                          <>
                            <span className="opacity-20">•</span>
                            <span className={`px-1.5 py-0.5 rounded-full border text-[8px] font-black ${
                              msg.topic === 'history' 
                                ? 'border-[#8B4513]/30 text-[#8B4513]/80 bg-[#8B4513]/5' 
                                : 'border-[#2E7D32]/30 text-[#2E7D32]/80 bg-[#2E7D32]/5'
                            }`}>
                              {msg.topic === 'history' ? '⚔ History' : '🌍 Geography'}
                            </span>
                          </>
                        )}
                        {isFullyRendered && msg.sources && msg.sources.length > 0 && (
                          <>
                            <span className="opacity-20">•</span>
                            <span className="px-1.5 py-0.5 rounded-full border border-[#D4AF37]/30 text-[8px] font-bold text-[#D4AF37] bg-[#D4AF37]/5">
                              📖 {msg.sources.join(' + ')}
                            </span>
                          </>
                        )}
                      </div>
                    </div>
                  );
                })
              )}
              
              {/* Fake typing loading indicators when waiting for network */}
              {isLoading && (
                <div className="flex flex-col items-start">
                  <div className="p-3 bg-white border border-[#2D1B14]/8 rounded-md rounded-tl-none shadow-sm flex items-center gap-1">
                    <span className="w-2 h-2 rounded-full bg-[#D4AF37] animate-bounce" style={{ animationDelay: '0s' }}></span>
                    <span className="w-2 h-2 rounded-full bg-[#D4AF37] animate-bounce" style={{ animationDelay: '0.2s' }}></span>
                    <span className="w-2 h-2 rounded-full bg-[#D4AF37] animate-bounce" style={{ animationDelay: '0.4s' }}></span>
                  </div>
                </div>
              )}

              <div ref={messagesEndRef} />
            </div>

            {/* INPUT PANEL FOOTER */}
            <form 
              onSubmit={(e) => { e.preventDefault(); handleSendMessage(input); }} 
              className={`h-[72px] bg-white border-t border-[#2D1B14]/8 px-4 flex items-center gap-3 shrink-0 ${isShaking ? 'animate-limit-shake border-red-500' : ''}`}
            >
              <Compass className="w-5 h-5 text-[#D4AF37]/75 shrink-0" />
              
              <textarea
                ref={textareaRef}
                value={input}
                onChange={handleInputChange}
                onKeyDown={handleKeyDownInput}
                disabled={isLoading || streamingId !== null}
                placeholder={placeholder}
                rows={1}
                className="flex-grow text-sm font-sans placeholder:opacity-40 text-[#2D1B14] border-0 focus:ring-0 resize-none outline-none overflow-y-auto bg-transparent py-2.5 max-h-[50px] scrollbar-none"
              />

              <button
                type="submit"
                disabled={!input.trim() || isLoading || streamingId !== null}
                className="w-9 h-9 flex items-center justify-center rounded-full bg-[#2D1B14] hover:bg-[#3d261c] text-[#D4AF37] shadow-md transition-all active:scale-95 disabled:opacity-30 disabled:cursor-not-allowed shrink-0 cursor-pointer"
              >
                {isLoading ? (
                  <Loader2 className="w-4 h-4 animate-spin text-[#D4AF37]" />
                ) : (
                  <Send className="w-4 h-4 text-[#D4AF37]" />
                )}
              </button>
            </form>
          </motion.div>
        )}
      </AnimatePresence>

      {/* CORE FLOATING TRIGGER BUTTON */}
      <div className="relative pointer-events-auto">
        <button
          onClick={() => setIsOpen(prev => !prev)}
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
          style={{
            boxShadow: showTooltip ? '0 0 0 4px rgba(212,175,55,0.15)' : 'none'
          }}
          className="w-14 h-14 rounded-full bg-[#2D1B14] text-[#F5F2E7] border-1.5 border-[#D4AF37] flex items-center justify-center hover:scale-108 active:scale-95 transition-all duration-300 shadow-[0_12px_40px_rgba(45,27,20,0.4)] relative cursor-pointer"
        >
          <AnimatePresence mode="wait">
            {isOpen ? (
              <motion.span 
                key="x"
                initial={{ rotate: -90, opacity: 0 }}
                animate={{ rotate: 0, opacity: 1 }}
                exit={{ rotate: 90, opacity: 0 }}
                transition={{ duration: 0.12 }}
                className="text-lg font-bold font-sans tracking-normal leading-none"
              >
                ✕
              </motion.span>
            ) : (
              <motion.div
                key="book"
                initial={{ rotate: 90, opacity: 0 }}
                animate={{ rotate: 0, opacity: 1 }}
                exit={{ rotate: -90, opacity: 0 }}
                transition={{ duration: 0.12 }}
              >
                <BookSparkleIcon />
              </motion.div>
            )}
          </AnimatePresence>

          {/* DYNAMIC PENDING/UNREAD INDICATORS */}
          {!isOpen && (
            unreadCount > 0 ? (
              <span className="absolute -top-1 -right-1 z-10 min-w-[18px] h-[18px] bg-[#D4AF37] text-[#2D1B14] px-1 font-sans text-[10px] font-bold rounded-full flex items-center justify-center border border-[#2D1B14] leading-none transition-transform duration-300">
                {unreadCount}
              </span>
            ) : (
              <span className="absolute -top-[1px] -right-[1px] z-10 w-[10px] h-[10px] rounded-full bg-[#D4AF37] border border-[#2D1B14] animate-ready-pulse" />
            )
          )}
        </button>

        {/* COMPACT TOOLTIP */}
        <AnimatePresence>
          {showTooltip && (
            <motion.div
              initial={{ opacity: 0, y: 10, scale: 0.95 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 10, scale: 0.95 }}
              transition={{ duration: 0.15 }}
              className="absolute bottom-16 right-0 bg-[#2D1B14] text-[#F5F2E7] p-3 rounded-sm shadow-xl border border-[#D4AF37]/35 text-center flex flex-col items-center gap-1.5 whitespace-nowrap z-[1000]"
            >
              <span className="text-[10px] uppercase font-bold tracking-[0.16em]">Ask the Scholar</span>
              <span className="text-[9px] font-bold text-[#D4AF37] opacity-65 tracking-widest leading-none bg-black/15 px-1.5 py-1 rounded-sm">Alt + S</span>
              {/* Arrow downwards */}
              <div className="absolute top-full right-6 w-2.5 h-2.5 bg-[#2D1B14] border-r border-b border-[#D4AF37]/35 rotate-45 -translate-y-[5px]" />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
};

export default ChatAssistant;
