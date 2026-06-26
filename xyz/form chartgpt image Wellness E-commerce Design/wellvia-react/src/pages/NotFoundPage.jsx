import { useNavigate } from 'react-router-dom';
import { LeafMark } from '../components/Logo';

export default function NotFoundPage() {
  const navigate = useNavigate();
  return (
    <main className="min-h-[74vh] flex items-start justify-center pt-[clamp(40px,8vh,90px)] px-6 text-center">
      <div className="animate-rise">
        <div className="mx-auto mb-4 w-fit opacity-50"><LeafMark size={64} /></div>
        <div className="font-serif text-[clamp(80px,12vw,140px)] leading-none text-green">404</div>
        <h1 className="font-serif font-medium text-[clamp(24px,3vw,34px)] mt-1.5 mb-2.5">This page wandered off</h1>
        <p className="text-[14.5px] text-muted max-w-[380px] mx-auto mb-6 font-light">The page you're looking for doesn't exist — but your next ritual does.</p>
        <button onClick={() => navigate('/')} className="bg-green text-white border-0 rounded-full px-[34px] py-[15px] text-[14px] cursor-pointer hover:bg-greenh">Back to Wellvia</button>
      </div>
    </main>
  );
}
