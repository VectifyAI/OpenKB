import { useRef } from "react"

/**
 * Frequency gate for section-switch enter animations (Apple §3 interruptibility):
 * returns false when the previous change to `key` was < `minGapMs` ago, so a
 * fast run through the nav renders instantly instead of replaying a spring the
 * user is already clicking past. A settled selection (gap >= minGapMs) returns
 * true. No change to `key` returns false (nothing to animate).
 */
export function useAnimatedSwitch(key: string, minGapMs = 260): boolean {
  const last = useRef<{ key: string; at: number }>({ key, at: 0 })
  if (key !== last.current.key) {
    const now = performance.now()
    const animate = now - last.current.at >= minGapMs
    last.current = { key, at: now }
    return animate
  }
  return false
}
