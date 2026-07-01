import L from 'leaflet';

export function createVesselIcon(heading: number, color = '#00ff88'): L.DivIcon {
  return L.divIcon({
    className: '',
    iconAnchor: [14, 14],
    html: `<div style="width:28px;height:28px;display:flex;align-items:center;justify-content:center;transform:rotate(${heading}deg);transition:transform 0.1s linear;">
      <svg viewBox="0 0 28 28" width="28" height="28" xmlns="http://www.w3.org/2000/svg">
        <polygon points="14,2 21,24 14,19 7,24" fill="${color}" stroke="#fff" stroke-width="1"/>
        <circle cx="14" cy="14" r="2.5" fill="#080c10"/>
      </svg>
    </div>`,
  });
}

export function createObstacleIcon(): L.DivIcon {
  return L.divIcon({
    className: '',
    iconAnchor: [10, 10],
    html: `<div style="width:20px;height:20px;border-radius:50%;background:rgba(255,59,92,0.6);border:2px solid #ff3b5c;box-sizing:border-box;box-shadow:0 0 8px #ff3b5c88;"/>`,
  });
}

export function createTrashIcon(): L.DivIcon {
  return L.divIcon({
    className: '',
    iconAnchor: [10, 10],
    html: `<div style="width:20px;height:20px;border-radius:4px;background:rgba(255,184,0,0.6);border:2px solid #ffb800;box-sizing:border-box;box-shadow:0 0 8px #ffb80088;"/>`,
  });
}

export function createGCSIcon(): L.DivIcon {
  return L.divIcon({
    className: '',
    iconAnchor: [12, 12],
    html: `<div style="width:24px;height:24px;border-radius:4px;background:#00ff88;border:2px solid #fff;display:flex;align-items:center;justify-content:center;font-family:monospace;font-size:8px;font-weight:700;color:#080c10;box-shadow:0 0 10px #00ff8888;">G</div>`,
  });
}

export function createWaypointDot(index: number): L.DivIcon {
  return L.divIcon({
    className: '',
    iconAnchor: [8, 8],
    html: `<div style="width:16px;height:16px;border-radius:50%;background:#00d4ff;border:1.5px solid #fff;display:flex;align-items:center;justify-content:center;font-family:monospace;font-size:7px;font-weight:700;color:#080c10;">${index}</div>`,
  });
}

export function createLookaheadIcon(): L.DivIcon {
  return L.divIcon({
    className: '',
    iconAnchor: [6, 6],
    html: `<div style="width:12px;height:12px;border-radius:50%;background:rgba(255,184,0,0.3);border:1.5px solid #ffb800;box-sizing:border-box;"/>`,
  });
}

export function createTargetIcon(): L.DivIcon {
  return L.divIcon({
    className: '',
    iconAnchor: [8, 8],
    html: `<div style="width:16px;height:16px;border-radius:50%;border:1.5px dashed #ffb800;box-sizing:border-box;"/>`,
  });
}
