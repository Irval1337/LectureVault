import { cn } from '../../lib/utils'

export function ProgressMeter({ label, value, max = 100, showPercent = false, className }: {
  label: string; value?: number; max?: number; showPercent?: boolean; className?: string
}) {
  const bounded = value === undefined ? undefined : Math.min(max, Math.max(0, value))
  return <div className={cn('progress-meter', className)}>
    <span className="progress-track" data-indeterminate={bounded === undefined ? '' : undefined}>
      <progress aria-label={label} value={bounded} max={max}/>
    </span>
    {showPercent && bounded !== undefined && <span className="progress-percent" aria-hidden="true">{Math.round(bounded / max * 100)}%</span>}
  </div>
}
