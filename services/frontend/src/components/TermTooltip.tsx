import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { CircleHelp } from "lucide-react";
import { FINANCIAL_TERMS, type FinancialTerm } from "@/lib/financialTerms";

interface TermTooltipProps {
  children: ReactNode;
  definition?: string;
  term?: FinancialTerm;
  className?: string;
}

export default function TermTooltip({
  children,
  definition,
  term,
  className = "",
}: TermTooltipProps) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const rootRef = useRef<HTMLSpanElement>(null);
  const description = definition ?? (term ? FINANCIAL_TERMS[term] : "");

  useEffect(() => {
    if (!open) return;
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  if (!description) return <>{children}</>;

  return (
    <span
      ref={rootRef}
      className={`tooltip-trigger relative ${className}`}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => {
        if (!rootRef.current?.contains(document.activeElement)) setOpen(false);
      }}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
      }}
    >
      <span>{children}</span>
      <button
        type="button"
        className="inline-grid h-4 w-4 place-items-center rounded-full text-[var(--muted-foreground)] hover:text-[var(--info)]"
        aria-label={`Explain ${typeof children === "string" ? children : "financial term"}`}
        aria-describedby={open ? id : undefined}
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        onFocus={() => setOpen(true)}
      >
        <CircleHelp className="h-3.5 w-3.5" aria-hidden="true" />
      </button>
      {open && (
        <span id={id} role="tooltip" className="tooltip-popover left-0 top-full mt-2">
          {description}
        </span>
      )}
    </span>
  );
}
