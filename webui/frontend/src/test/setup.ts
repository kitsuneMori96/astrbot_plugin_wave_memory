import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

afterEach(() => cleanup())

class ResizeObserverMock implements ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

Object.defineProperty(globalThis, 'ResizeObserver', { writable: true, value: ResizeObserverMock })
Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { writable: true, value: vi.fn() })
Object.defineProperty(HTMLElement.prototype, 'hasPointerCapture', { writable: true, value: () => false })
Object.defineProperty(HTMLElement.prototype, 'setPointerCapture', { writable: true, value: vi.fn() })
Object.defineProperty(HTMLElement.prototype, 'releasePointerCapture', { writable: true, value: vi.fn() })

export function setViewport(width: number) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: width })
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: width < 768,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  })
  window.dispatchEvent(new Event('resize'))
}
