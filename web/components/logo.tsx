import { useId } from "react";

/** Dockra mark: a "D" whose counter holds an upward chevron — code going up to the cloud. */
export function LogoMark({ size = 28, className = "" }: { size?: number; className?: string }) {
  const id = useId();
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden className={`shrink-0 ${className}`}>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#818cf8" />
          <stop offset="1" stopColor="#3b82f6" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="8" fill={`url(#${id})`} />
      <path d="M10 9h5.5a7 7 0 0 1 0 14H10z" fill="none" stroke="#fff" strokeWidth="2.4" strokeLinejoin="round" />
      <path d="M13.5 18.5 16 16l2.5 2.5" fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Wordmark({ tagline = false }: { tagline?: boolean }) {
  return (
    <span className="flex items-center gap-2.5">
      <LogoMark />
      <span className="leading-tight">
        <span className="block text-[15px] font-semibold tracking-tight text-fg">Dockra</span>
        {tagline && <span className="block text-[11px] text-muted">From code to cloud.</span>}
      </span>
    </span>
  );
}
