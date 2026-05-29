import { useEffect } from 'react';
import { connectWebSocket, disconnectWebSocket } from '../services/websocket';
import { useDashboardStore } from '../store/dashboardStore';

export function useWebSocket() {
  const backendUrl = useDashboardStore((s) => s.backendUrl);

  useEffect(() => {
    connectWebSocket();
    return () => disconnectWebSocket();
  }, [backendUrl]);
}