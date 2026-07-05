/** Top-level route composition for the AdaptIQ React app. */

import { Routes, Route } from 'react-router-dom';
import Home from './pages/Home';
import Login from './pages/Login';
import Signup from './pages/Signup';
import Dashboard from './pages/Dashboard';
import ClassicRoom from './pages/ClassicRoom';
import ChallengeRoom from './pages/ChallengeRoom';
import CustomRoom from './pages/CustomRoom';
import Profile from './pages/Profile';
import ForgotPassword from './pages/ForgotPassword';
import ResetPassword from './pages/ResetPassword';
import AdminDashboard from './pages/AdminDashboard';
import PvPRoom from './pages/PvPRoom';
import VisualRoomQuiz from './pages/VisualRoomQuiz';
import ChatAssistant from './components/ChatAssistant';
import { AdminRoute, ProtectedRoute } from './components/RouteGuards';
import { useAuth } from './context/AuthContext';

export default function App() {
  const { user } = useAuth();

  return (
    <>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/login" element={<Login />} />
        <Route path="/signup" element={<Signup />} />
        <Route path="/dashboard" element={<ProtectedRoute><Dashboard /></ProtectedRoute>} />
        <Route path="/rooms/classic" element={<ProtectedRoute><ClassicRoom /></ProtectedRoute>} />
        <Route path="/rooms/challenge" element={<ProtectedRoute><ChallengeRoom /></ProtectedRoute>} />
        <Route path="/rooms/custom" element={<ProtectedRoute><CustomRoom /></ProtectedRoute>} />
        <Route path="/rooms/pvp" element={<ProtectedRoute><PvPRoom /></ProtectedRoute>} />
        <Route path="/rooms/visual" element={<ProtectedRoute><VisualRoomQuiz /></ProtectedRoute>} />
        <Route path="/profile" element={<ProtectedRoute><Profile /></ProtectedRoute>} />
        <Route path="/admin" element={<AdminRoute><AdminDashboard /></AdminRoute>} />
        <Route path="/forgot-password" element={<ForgotPassword />} />
        <Route path="/reset-password" element={<ResetPassword />} />
      </Routes>
      {user && <ChatAssistant />}
    </>
  );
}
