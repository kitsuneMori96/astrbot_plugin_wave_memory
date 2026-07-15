export const EXPLORE_FRAME_MESSAGE_SOURCE = 'wavememory.explore'

export type ExploreFrameMessage =
  | { source: typeof EXPLORE_FRAME_MESSAGE_SOURCE; type: 'initial-load-ready'; message: string }
  | { source: typeof EXPLORE_FRAME_MESSAGE_SOURCE; type: 'initial-load-error'; message: string }

export type ExploreFrameState =
  | { status: 'loading' }
  | { status: 'ready'; message: string }
  | { status: 'error'; message: string }
  | { status: 'timeout'; message: string }

export type ExploreFrameAction =
  | { type: 'reset' }
  | { type: 'message'; message: ExploreFrameMessage }
  | { type: 'timeout' }

export function buildExploreFrameUrl(botId: string, sessionId: string): string {
  const query = new URLSearchParams({ bot_id: botId, session_id: sessionId, visibility: 'group', embed: '1' })
  return `/explore?${query.toString()}`
}

export function parseExploreFrameMessage(
  event: Pick<MessageEvent, 'data' | 'origin' | 'source'>,
  expectedSource: MessageEventSource | null,
  expectedOrigin: string,
): ExploreFrameMessage | null {
  if (!expectedSource || event.origin !== expectedOrigin || event.source !== expectedSource) {
    return null
  }

  const data = event.data
  if (typeof data !== 'object' || data === null || data.source !== EXPLORE_FRAME_MESSAGE_SOURCE) {
    return null
  }
  if (data.type !== 'initial-load-ready' && data.type !== 'initial-load-error') {
    return null
  }

  return {
    source: EXPLORE_FRAME_MESSAGE_SOURCE,
    type: data.type,
    message: typeof data.message === 'string' && data.message.trim()
      ? data.message
      : data.type === 'initial-load-ready'
        ? '图谱已就绪。'
        : '图谱初次加载失败。',
  }
}

export function nextExploreFrameState(_state: ExploreFrameState, action: ExploreFrameAction): ExploreFrameState {
  if (action.type === 'reset') {
    return { status: 'loading' }
  }
  if (action.type === 'timeout') {
    return { status: 'timeout', message: '图谱加载超时，请重试或检查服务端状态。' }
  }
  if (action.message.type === 'initial-load-ready') {
    return { status: 'ready', message: action.message.message }
  }
  return { status: 'error', message: action.message.message }
}
