import { useEffect, useState } from 'react';
import type { MotorState, SteeringCmd } from '../types';

interface Props { motor: MotorState; steering: SteeringCmd; }

export function MotorIndicator({ motor, steering }: Props) {
  const [blink, setBlink] = useState(false);

  useEffect(() => {
    if (!motor.blinking) { setBlink(false); return; }
    const iv = setInterval(() => setBlink((b) => !b), 350);
    return () => clearInterval(iv);
  }, [motor.blinking]);

  const leftOn  = motor.blinking ? blink        : motor.left;
  const rightOn = motor.blinking ? !blink       : motor.right;

  const steeringLabels: Record<SteeringCmd, string> = {
    forward:          'FORWARD',
    turn_left:        'TURN LEFT',
    turn_right:       'TURN RIGHT',
    stop:             'STOP',
    large_correction: 'CORRECTING',
  };

  const steeringColors: Record<SteeringCmd, string> = {
    forward:          '#00ff88',
    turn_left:        '#00d4ff',
    turn_right:       '#00d4ff',
    stop:             '#ff3b5c',
    large_correction: '#ffb800',
  };

  return (
    <div className="flex flex-col items-center gap-3">
      {/* LED pair */}
      <div className="flex items-center gap-4">
        <LED label="LEFT" on={leftOn} />
        <div className="flex flex-col items-center">
          <span className="text-xs font-mono text-gray-600 uppercase tracking-widest">MOTORS</span>
        </div>
        <LED label="RIGHT" on={rightOn} />
      </div>

      {/* Steering command */}
      <div
        className="px-4 py-1.5 rounded font-mono text-xs font-bold tracking-widest uppercase text-center w-full"
        style={{
          background: `${steeringColors[steering]}18`,
          border: `1px solid ${steeringColors[steering]}44`,
          color: steeringColors[steering],
        }}
      >
        {steeringLabels[steering]}
      </div>
    </div>
  );
}

function LED({ label, on }: { label: string; on: boolean }) {
  return (
    <div className="flex flex-col items-center gap-1.5">
      <div
        className="w-10 h-10 rounded-full transition-all duration-100 flex items-center justify-center"
        style={{
          background: on ? 'rgba(0,255,136,0.25)' : 'rgba(255,255,255,0.04)',
          border: on ? '2px solid #00ff88' : '2px solid rgba(255,255,255,0.1)',
          boxShadow: on ? '0 0 18px #00ff8888, inset 0 0 8px #00ff8844' : 'none',
        }}
      >
        <div
          className="w-5 h-5 rounded-full"
          style={{
            background: on ? '#00ff88' : 'rgba(255,255,255,0.06)',
            boxShadow: on ? '0 0 8px #00ff88' : 'none',
          }}
        />
      </div>
      <span className="text-xs font-mono" style={{ color: on ? '#00ff88' : 'rgba(255,255,255,0.2)' }}>
        {label}
      </span>
    </div>
  );
}