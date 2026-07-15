import { describe, expect, it } from 'vitest'

import {
  EXPLORE_FRAME_MESSAGE_SOURCE,
  buildExploreFrameUrl,
  nextExploreFrameState,
  parseExploreFrameMessage,
  type ExploreFrameState,
} from '@/lib/explore-frame'

describe('Explore iframe 协议', () => {
  it('iframe URL 只包含 scope，不包含任何 token query', () => {
    const url = buildExploreFrameUrl('bot:yushu', 'qq:group:42')
    const query = new URLSearchParams(url.split('?')[1])

    expect(query.get('bot_id')).toBe('bot:yushu')
    expect(query.get('session_id')).toBe('qq:group:42')
    expect(query.get('visibility')).toBe('group')
    expect(query.get('embed')).toBe('1')
    expect([...query.keys()].sort()).toEqual(['bot_id', 'embed', 'session_id', 'visibility'])
    expect(query.has('token')).toBe(false)
  })

  it('严格过滤非同源、非当前 iframe 和未知消息', () => {
    const iframeWindow = {} as MessageEventSource
    const validData = {
      source: EXPLORE_FRAME_MESSAGE_SOURCE,
      type: 'initial-load-ready',
      message: 'ready',
    }

    expect(parseExploreFrameMessage({ data: validData, origin: 'https://evil.example', source: iframeWindow }, iframeWindow, 'https://wave.example')).toBeNull()
    expect(parseExploreFrameMessage({ data: validData, origin: 'https://wave.example', source: {} as MessageEventSource }, iframeWindow, 'https://wave.example')).toBeNull()
    expect(parseExploreFrameMessage({ data: { ...validData, source: 'other' }, origin: 'https://wave.example', source: iframeWindow }, iframeWindow, 'https://wave.example')).toBeNull()
    expect(parseExploreFrameMessage({ data: { ...validData, type: 'query-error' }, origin: 'https://wave.example', source: iframeWindow }, iframeWindow, 'https://wave.example')).toBeNull()
  })

  it('把合法初次加载消息与 timeout 转换为明确状态', () => {
    const iframeWindow = {} as MessageEventSource
    const event = {
      origin: 'https://wave.example',
      source: iframeWindow,
      data: { source: EXPLORE_FRAME_MESSAGE_SOURCE, type: 'initial-load-error', message: 'HTTP 503' },
    }
    const message = parseExploreFrameMessage(event, iframeWindow, 'https://wave.example')
    expect(message).not.toBeNull()

    let state: ExploreFrameState = { status: 'loading' }
    state = nextExploreFrameState(state, { type: 'message', message: message! })
    expect(state).toEqual({ status: 'error', message: 'HTTP 503' })
    expect(nextExploreFrameState(state, { type: 'reset' })).toEqual({ status: 'loading' })
    expect(nextExploreFrameState({ status: 'loading' }, { type: 'timeout' })).toEqual({
      status: 'timeout',
      message: '图谱加载超时，请重试或检查服务端状态。',
    })
  })
})
