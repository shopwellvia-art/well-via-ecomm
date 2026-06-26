/**
 * StepIndicator — Shipping → Payment → Review progress row for checkout.
 * `step` is 1-based. `onStep(n)` lets the user jump back.
 */
const STEPS = [
  { num: 1, label: 'Shipping' },
  { num: 2, label: 'Payment' },
  { num: 3, label: 'Review' },
];

export default function StepIndicator({ step, onStep }) {
  return (
    <div className="flex items-center justify-center gap-2 sm:gap-4 mb-7 lg:mb-10">
      {STEPS.map((s, i) => {
        const active = step === s.num;
        const done = step > s.num;
        const circleBg = active ? 'bg-green text-white border-transparent' : done ? 'bg-gold text-white border-transparent' : 'bg-transparent text-muted border-line';
        return (
          <div key={s.num} className="flex items-center gap-2 sm:gap-4">
            <button onClick={() => onStep?.(s.num)} className="flex items-center gap-2.5 cursor-pointer bg-transparent border-0 p-0">
              <span className={`w-[30px] h-[30px] rounded-full border flex items-center justify-center text-[13px] shrink-0 ${circleBg}`}>
                {s.num}
              </span>
              <span className={`text-[13.5px] tracking-wide ${active ? 'text-ink' : 'text-muted'}`}>{s.label}</span>
            </button>
            {i < STEPS.length - 1 && <span className="text-line">→</span>}
          </div>
        );
      })}
    </div>
  );
}
