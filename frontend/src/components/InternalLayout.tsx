/** Shared UI component for InternalLayout behavior. */

import React, { useState } from 'react';
import ChatAssistant from './ChatAssistant';
import { useNavigate, useLocation } from 'react-router-dom';
import { 
  LayoutDashboard, 
  BookOpen, 
  Shield,
  UserCircle2,
  LogOut,
  Library,
  ChevronRight
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';

interface InternalLayoutProps {
  children: React.ReactNode;
  forceRoomsOpen?: boolean;
}

type RoomNavItem = {
  name: string;
  path: string;
  id: string;
  disabled?: boolean;
};

const InternalLayout: React.FC<InternalLayoutProps> = ({ children, forceRoomsOpen }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuth();
  const [isRoomsOpen, setIsRoomsOpen] = useState(true);

  const effectiveRoomsOpen = forceRoomsOpen || isRoomsOpen;

  const navItems = [
    { name: 'Dashboard', path: '/dashboard', icon: <LayoutDashboard className="w-5 h-5" /> },
    { name: 'Profile', path: '/profile', icon: <UserCircle2 className="w-5 h-5" /> },
    ...(user?.is_admin ? [{ name: 'Admin', path: '/admin', icon: <Shield className="w-5 h-5" /> }] : []),
  ];

  const rooms: RoomNavItem[] = [
    { name: 'Classic Room', path: '/rooms/classic', id: 'classic' },
    { name: 'Challenge Room', path: '/rooms/challenge', id: 'challenge' },
    { name: 'Custom Room', path: '/rooms/custom', id: 'custom' },
    { name: 'PvP Room', path: '/rooms/pvp', id: 'pvp' },
    { name: 'Visual Room', path: '/rooms/visual', id: 'visual' },
  ];

  return (
    <div className="flex min-h-screen bg-[#F5F2E7] text-[#2D1B14] font-serif selection:bg-[#D4AF37]/25">
      {/* Sidebar */}
      <aside className="w-64 bg-gradient-to-b from-[#2D1B14] via-[#2D1B14] to-[#231510] text-[#F5F2E7] flex flex-col border-r border-[#D4AF37]/20 shadow-xl">
        <div className="p-8 flex items-center gap-3 border-b border-[#D4AF37]/15">
          <div className="w-10 h-10 bg-[#D4AF37] rounded-md flex items-center justify-center rotate-3 shadow-lg shadow-[#D4AF37]/20 hover:rotate-0 transition-transform duration-500">
            <Library className="text-[#2D1B14] w-6 h-6" />
          </div>
          <span className="text-xl font-black font-playfair tracking-tight">Adapti<span className="text-[#D4AF37]">Q</span></span>
        </div>

        <nav className="flex-grow py-8 px-4 space-y-2">
          {navItems.map((item) => (
            <button
              key={item.name}
              onClick={() => navigate(item.path)}
              className={`w-full flex items-center gap-4 px-4 py-3 rounded-md transition-all duration-300 text-sm font-bold uppercase tracking-widest ${
                location.pathname === item.path 
                  ? 'bg-[#D4AF37] text-[#2D1B14] shadow-lg shadow-[#D4AF37]/20' 
                  : 'text-[#F5F2E7]/60 hover:text-[#F5F2E7] hover:bg-white/[0.07]'
              }`}
            >
              {item.icon}
              {item.name}
            </button>
          ))}

          {/* Collapsible Rooms Menu */}
          <div>
            <button
              onClick={() => setIsRoomsOpen(!isRoomsOpen)}
              className={`w-full flex items-center justify-between px-4 py-3 rounded-md transition-all duration-300 text-sm font-bold uppercase tracking-widest text-[#F5F2E7]/60 hover:text-[#F5F2E7] hover:bg-white/[0.07]`}
            >
              <div className="flex items-center gap-4">
                <BookOpen className="w-5 h-5" />
                <span>Rooms</span>
              </div>
              <ChevronRight className={`w-4 h-4 transition-transform duration-300 ${effectiveRoomsOpen ? 'rotate-90' : ''}`} />
            </button>
            
            <div className={`overflow-hidden transition-all duration-500 ease-in-out ${effectiveRoomsOpen ? 'max-h-64 opacity-100 mt-2' : 'max-h-0 opacity-0'}`}>
              <div className="pl-12 space-y-1">
                {rooms.map((room) => (
                  <button
                    key={room.id}
                    id={`sidebar-room-${room.id}`}
                    disabled={room.disabled}
                    onClick={() => !room.disabled && navigate(room.path)}
                    className={`w-full text-left py-2.5 text-[11px] font-bold uppercase tracking-[0.18em] transition-all duration-200 ${
                      location.pathname === room.path 
                        ? 'text-[#D4AF37] translate-x-1' 
                        : 'text-[#F5F2E7]/40 hover:text-[#F5F2E7]/80 hover:translate-x-0.5'
                    } ${room.disabled ? 'opacity-20 cursor-not-allowed' : ''}`}
                  >
                    {room.name}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </nav>

        <div className="p-8 border-t border-[#D4AF37]/15">
          <button 
            onClick={() => {
              logout();
              navigate('/login');
            }}
            className="w-full flex items-center gap-4 px-4 py-3 rounded-md text-[#F5F2E7]/60 hover:text-[#D4AF37] hover:bg-white/[0.05] transition-all duration-300 text-sm font-bold uppercase tracking-widest"
          >
            <LogOut className="w-5 h-5" />
            Logout
          </button>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-grow overflow-y-auto paper-texture scroll-smooth">
        <div className="max-w-[95%] mx-auto px-10 py-8">
          {children}
        </div>
      </main>

      <ChatAssistant />
    </div>
  );
};

export default InternalLayout;
