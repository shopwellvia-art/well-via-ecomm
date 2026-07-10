/**
 * StepIndicator — checkout progress row.
 *
 * Props:
 *   step    — current step number (1-based)
 *   onStep  — optional callback(n) — called when the user clicks a completed step
 *             to navigate back; active/future steps are non-interactive
 *   labels  — optional step label list (default: Bag → Address → Payment)
 */
const DEFAULT_LABELS = ['Bag', 'Address', 'Payment'];

export default function StepIndicator({ step, onStep, labels = DEFAULT_LABELS }) {
  const steps = labels.map((label, i) => ({ num: i + 1, label }));
  return (
    <div className="flex items-center justify-center gap-2 sm:gap-4 mb-7 lg:mb-10">
      {steps.map((s, i) => {
        const active = step === s.num;
        const done = step > s.num;

        // Circle background: active = wgreen, done = wgold, future = transparent
        const circleBg = active
          ? 'bg-wgreen text-white border-transparent'
          : done
          ? 'bg-wgold text-white border-transparent'
          : 'bg-transparent text-wmuted border-wline';

        return (
          <div key={s.num} className="flex items-center gap-2 sm:gap-4">
            <button
              onClick={() => done && onStep?.(s.num)}
              disabled={!done}
              className="flex items-center gap-2.5 bg-transparent border-0 p-0 disabled:cursor-default cursor-pointer"
            >
              <span
                className={`w-[30px] h-[30px] rounded-full border flex items-center justify-center text-[13px] shrink-0 transition-colors ${circleBg}`}
              >
                {s.num}
              </span>
              <span
                className={`text-[13.5px] tracking-wide transition-colors ${
                  active ? 'text-wink font-medium' : 'text-wmuted'
                }`}
              >
                {s.label}
              </span>
            </button>

            {/* Connector arrow between steps */}
            {i < steps.length - 1 && (
              <span className="text-wline text-[16px] select-none">→</span>
            )}
          </div>
        );
      })}
    </div>
  );
}
