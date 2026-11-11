import { cn } from '@/lib/cn'

/**
 * Single-series trend visual with soft gradient area fill and calm hairline baseline.
 */
export function Sparkline({ points, labels, height = 130, color = '#242220' }) {
  // Extract polygon coordinates for gradient fill below line
  // points is e.g. "0,100 80,60 160,80..."
  const pts = points.trim().split(' ').map((p) => p.split(',').map(Number))
  const firstX = pts[0]?.[0] ?? 0
  const lastX = pts[pts.length - 1]?.[0] ?? 560
  const areaPoints = `${points} ${lastX},139 ${firstX},139`

  return (
    <div className="flex flex-col gap-2">
      <svg
        viewBox="0 0 560 140"
        className="block w-full overflow-visible"
        style={{ height }}
        role="img"
        aria-label="Trend chart"
      >
        <defs>
          <linearGradient id="calmSparklineGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#38664f" stopOpacity="0.16" />
            <stop offset="85%" stopColor="#38664f" stopOpacity="0.01" />
            <stop offset="100%" stopColor="#38664f" stopOpacity="0" />
          </linearGradient>
        </defs>

        {/* Subtle background gridlines */}
        <line x1="0" y1="35" x2="560" y2="35" stroke="var(--hairline-soft)" strokeDasharray="3 3" strokeWidth="1" />
        <line x1="0" y1="80" x2="560" y2="80" stroke="var(--hairline-soft)" strokeDasharray="3 3" strokeWidth="1" />

        {/* Soft Area fill */}
        <polygon points={areaPoints} fill="url(#calmSparklineGrad)" />

        {/* Crisp Line */}
        <polyline
          points={points}
          fill="none"
          stroke={color}
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />

        {/* Baseline */}
        <line x1="0" y1="139" x2="560" y2="139" stroke="var(--hairline)" strokeWidth="1" />

        {/* Data points dots */}
        {pts.map(([x, y], i) => (
          <circle
            key={i}
            cx={x}
            cy={y}
            r={i === pts.length - 1 ? 3.5 : 2.5}
            fill="#ffffff"
            stroke={color}
            strokeWidth="1.75"
          />
        ))}
      </svg>

      {labels && (
        <div className="flex justify-between px-1 font-body text-[11px] font-medium text-muted">
          {labels.map((l) => (
            <span key={l}>{l}</span>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * Horizontal ranked bars with tranquil colors and clear progress track.
 */
export function BarList({ items }) {
  return (
    <div className="flex flex-col gap-3">
      {items.map((b) => (
        <div key={b.label} className="flex flex-col gap-1.5">
          <div className="flex justify-between font-body text-[13px]">
            <span className="font-medium text-ink">{b.label}</span>
            <span className="text-muted tabular-nums">{b.value}</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-strong/70">
            <div
              className="h-full rounded-full transition-all duration-500 ease-out"
              style={{
                background: b.color || 'var(--color-sage)',
                width: b.pct,
              }}
            />
          </div>
        </div>
      ))}
    </div>
  )
}

/**
 * Confidence meter for clinical AI suggestions.
 */
export function ConfidenceBar({ pct, label, color }) {
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-[90px] overflow-hidden rounded-full bg-strong">
        <div
          className="h-full rounded-full transition-all duration-300"
          style={{ background: color, width: pct }}
        />
      </div>
      <span className="font-body text-xs font-medium text-muted tabular-nums">{label}</span>
    </div>
  )
}
