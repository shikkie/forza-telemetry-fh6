export function convertSpeed(speedMs, unit = 'mph') {
  if (speedMs == null || isNaN(speedMs)) return 0

  if (unit === 'kmh') {
    return (speedMs * 3.6).toFixed(1)
  }
  // default to mph
  return (speedMs * 2.23694).toFixed(1)
}

export function getSpeedUnitLabel(unit = 'mph') {
  if (unit === 'kmh') return 'km/h'
  return 'mph'
}
