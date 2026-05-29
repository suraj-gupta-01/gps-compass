interface Props { heading: number; required: number; error: number; }

export function CompassWidget({ heading, required, error }: Props) {
  const pts = ['N','NE','E','SE','S','SW','W','NW'];
  const compassPoint = pts[Math.round(heading / 45) % 8];

  return (
    <div className="flex flex-col items-center gap-2">
      <div className="relative w-36 h-36">
        {/* Outer ring */}
        <svg viewBox="0 0 144 144" className="absolute inset-0 w-full h-full">
          <circle cx="72" cy="72" r="68" fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="1"/>
          <circle cx="72" cy="72" r="60" fill="none" stroke="rgba(255,255,255,0.04)" strokeWidth="8"/>
          {/* Cardinal tick marks */}
          {[0,45,90,135,180,225,270,315].map((deg) => {
            const rad = (deg - 90) * Math.PI / 180;
            const isMajor = deg % 90 === 0;
            const inner = isMajor ? 54 : 58;
            const outer = 66;
            const x1 = 72 + inner * Math.cos(rad);
            const y1 = 72 + inner * Math.sin(rad);
            const x2 = 72 + outer * Math.cos(rad);
            const y2 = 72 + outer * Math.sin(rad);
            return (
              <line key={deg} x1={x1} y1={y1} x2={x2} y2={y2}
                stroke={isMajor ? 'rgba(255,255,255,0.4)' : 'rgba(255,255,255,0.15)'}
                strokeWidth={isMajor ? 1.5 : 1} />
            );
          })}
          {/* Required heading indicator (amber arc) */}
          <line
            x1="72" y1="72"
            x2={72 + 52 * Math.cos((required - 90) * Math.PI / 180)}
            y2={72 + 52 * Math.sin((required - 90) * Math.PI / 180)}
            stroke="#ffb800" strokeWidth="1.5" strokeDasharray="4 3" opacity="0.7"
          />
          {/* Required heading dot */}
          <circle
            cx={72 + 62 * Math.cos((required - 90) * Math.PI / 180)}
            cy={72 + 62 * Math.sin((required - 90) * Math.PI / 180)}
            r="3" fill="#ffb800" opacity="0.9"
          />
        </svg>

        {/* Rotating compass rose */}
        <div
          className="absolute inset-0 flex items-center justify-center"
          style={{ transform: `rotate(${-heading}deg)`, transition: 'transform 0.3s ease' }}
        >
          <svg viewBox="0 0 120 120" className="w-28 h-28">
            {/* N pointer */}
            <polygon points="60,8 65,55 60,50 55,55" fill="#ff3b5c"/>
            {/* S pointer */}
            <polygon points="60,112 65,65 60,70 55,65" fill="rgba(255,255,255,0.3)"/>
            {/* E pointer */}
            <polygon points="112,60 65,55 70,60 65,65" fill="rgba(255,255,255,0.2)"/>
            {/* W pointer */}
            <polygon points="8,60 55,55 50,60 55,65" fill="rgba(255,255,255,0.2)"/>
            <circle cx="60" cy="60" r="4" fill="#080c10" stroke="rgba(255,255,255,0.3)" strokeWidth="1"/>
          </svg>
        </div>

        {/* Center readout */}
        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
          <span className="text-white font-mono font-bold text-sm leading-none">
            {String(Math.round(heading)).padStart(3,'0')}°
          </span>
          <span className="text-gray-400 font-mono text-xs">{compassPoint}</span>
        </div>
      </div>

      {/* Heading error bar */}
      <div className="w-full">
        <div className="flex justify-between text-xs font-mono mb-1">
          <span className="text-gray-600">HDG ERROR</span>
          <span className={Math.abs(error) > 20 ? 'text-accent-red' : Math.abs(error) > 8 ? 'text-accent-amber' : 'text-accent-green'}>
            {error > 0 ? '+' : ''}{Math.round(error)}°
          </span>
        </div>
        <div className="relative h-2 rounded-full overflow-hidden" style={{background:'rgba(255,255,255,0.06)'}}>
          <div className="absolute top-0 bottom-0 w-px bg-white/20" style={{left:'50%'}}/>
          <div
            className="absolute top-0 bottom-0 rounded-full transition-all"
            style={{
              background: Math.abs(error) > 20 ? '#ff3b5c' : Math.abs(error) > 8 ? '#ffb800' : '#00ff88',
              width: `${Math.min(Math.abs(error) / 180 * 50, 50)}%`,
              left: error >= 0 ? '50%' : `${50 - Math.min(Math.abs(error) / 180 * 50, 50)}%`,
            }}
          />
        </div>
        <div className="flex justify-between text-xs font-mono text-gray-700 mt-0.5">
          <span>-180°</span><span>+180°</span>
        </div>
      </div>
    </div>
  );
}