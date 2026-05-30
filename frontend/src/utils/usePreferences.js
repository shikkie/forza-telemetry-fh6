import { useState, useEffect } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE || ''

export function usePreferences() {
  const [preferences, setPreferences] = useState({
    speed_unit: 'mph',   // default
    power_unit: 'hp',    // default
  })
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    async function fetchPrefs() {
      try {
        const res = await fetch(`${API_BASE}/api/preferences`)
        if (res.ok) {
          const data = await res.json()
          setPreferences({
            speed_unit: data.speed_unit || 'mph',
            power_unit: data.power_unit || 'hp',
          })
        }
      } catch (e) {
        // fallback to defaults
        console.warn('Could not load preferences, using defaults')
      } finally {
        setLoading(false)
      }
    }
    fetchPrefs()
  }, [])

  return { ...preferences, loading }
}
