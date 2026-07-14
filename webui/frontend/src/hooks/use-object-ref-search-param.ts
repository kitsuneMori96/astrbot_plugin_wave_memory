import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'

export function useObjectRefSearchParam(key = 'ref') {
  const [searchParams, setSearchParams] = useSearchParams()
  const ref = searchParams.get(key)
  const setRef = useCallback((nextRef: string | null, replace = false) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      if (nextRef) next.set(key, nextRef)
      else next.delete(key)
      return next
    }, { replace })
  }, [key, setSearchParams])

  return [ref, setRef] as const
}
