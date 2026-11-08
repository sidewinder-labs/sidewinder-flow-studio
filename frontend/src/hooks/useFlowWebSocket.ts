import { useEffect, useRef, useCallback } from 'react'
import type { WsEvent } from '@/types'

type Handler = (event: WsEvent) => void

/** `onReconnect` runs when the connection comes back after a drop: the
 *  backend may have restarted, taking a run with it. */
export function useFlowWebSocket(flowId: string | null, onEvent: Handler, onReconnect?: () => void) {
  const wsRef = useRef<WebSocket | null>(null)
  const handlerRef = useRef<Handler>(onEvent)
  handlerRef.current = onEvent
  const reconnectRef = useRef(onReconnect)
  reconnectRef.current = onReconnect
  const droppedRef = useRef(false)

  const connect = useCallback(() => {
    if (!flowId) return
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${protocol}://${window.location.host}/ws/flow/${flowId}`
    const ws = new WebSocket(url)
    ws.onopen = () => {
      if (droppedRef.current) { droppedRef.current = false; reconnectRef.current?.() }
    }

    ws.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data) as WsEvent
        handlerRef.current(event)
      } catch { /* ignore */ }
    }

    ws.onclose = () => {
      if (wsRef.current === ws) droppedRef.current = true
      // Reconnect after 2s if closed unexpectedly
      setTimeout(() => {
        if (wsRef.current?.readyState === WebSocket.CLOSED) {
          connect()
        }
      }, 2000)
    }

    // Keepalive ping every 20s
    const pingInterval = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send('ping')
    }, 20_000)

    ws.addEventListener('close', () => clearInterval(pingInterval))

    wsRef.current = ws
  }, [flowId])

  useEffect(() => {
    connect()
    return () => {
      wsRef.current?.close()
      wsRef.current = null
    }
  }, [connect])
}
