import L from 'leaflet';

export function createVesselIcon(heading: number): L.DivIcon {
  return L.divIcon({
    className: '',
    iconAnchor: [20, 20],
    html: `<div style="width:40px;height:40px;display:flex;align-items:center;justify-content:center;transform:rotate(${heading}deg);transition:transform 0.2s linear;">
      <svg viewBox="0 0 40 40" width="40" height="40" xmlns="http://www.w3.org/2000/svg">
        <polygon points="20,3 30,34 20,27 10,34" fill="#00ff88" stroke="#fff" stroke-width="1.5"/>
        <circle cx="20" cy="20" r="3.5" fill="#080c10"/>
        <circle cx="20" cy="20" r="3.5" fill="#00ff88" opacity="0.5">
          <animate attributeName="r" values="3.5;9;3.5" dur="2s" repeatCount="indefinite"/>
          <animate attributeName="opacity" values="0.5;0;0.5" dur="2s" repeatCount="indefinite"/>
        </circle>
      </svg>
    </div>`,
  });
}

export function createTargetIcon(): L.DivIcon {
  return L.divIcon({
    className: '',
    iconAnchor: [12, 12],
    html: `<div style="width:24px;height:24px;display:flex;align-items:center;justify-content:center;">
      <svg viewBox="0 0 24 24" width="24" height="24">
        <circle cx="12" cy="12" r="10" fill="none" stroke="#ffb800" stroke-width="1.5" stroke-dasharray="4 3"/>
        <circle cx="12" cy="12" r="3" fill="#ffb800"/>
        <line x1="12" y1="2" x2="12" y2="7" stroke="#ffb800" stroke-width="1.5"/>
        <line x1="12" y1="17" x2="12" y2="22" stroke="#ffb800" stroke-width="1.5"/>
        <line x1="2" y1="12" x2="7" y2="12" stroke="#ffb800" stroke-width="1.5"/>
        <line x1="17" y1="12" x2="22" y2="12" stroke="#ffb800" stroke-width="1.5"/>
      </svg>
    </div>`,
  });
}

export function createGCSIcon(): L.DivIcon {
  return L.divIcon({
    className: '',
    iconAnchor: [20, 20],
    html: `<div style="width:40px;height:40px;border-radius:6px;background:#00ff88;border:2px solid #fff;display:flex;align-items:center;justify-content:center;font-family:'Space Mono',monospace;font-size:9px;font-weight:700;color:#080c10;box-shadow:0 0 16px #00ff8899;letter-spacing:-0.5px;">GCS</div>`,
  });
}

export function createWaypointDot(index: number): L.DivIcon {
  return L.divIcon({
    className: '',
    iconAnchor: [10, 10],
    html: `<div style="width:20px;height:20px;border-radius:50%;background:#00d4ff;border:1.5px solid #fff;display:flex;align-items:center;justify-content:center;font-family:'Space Mono',monospace;font-size:8px;font-weight:700;color:#080c10;box-shadow:0 0 6px #00d4ff66;">${index}</div>`,
  });
}