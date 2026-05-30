/**
 * Formats a duration in seconds to a human-readable racing style string.
 * Matches requested style "nH mm:ss.000"
 * Examples:
 *   461.8   → "0H 07:41.800"
 *   3725.3  → "1H 02:05.300"
 *   45.23   → "0H 00:45.230"
 */
export function formatDuration(seconds) {
  if (seconds == null || !isFinite(seconds) || seconds <= 0) {
    return '—';
  }

  const totalSeconds = Math.floor(seconds);
  const milliseconds = Math.round((seconds - totalSeconds) * 1000);

  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const secs = totalSeconds % 60;

  const hh = hours.toString();
  const mm = minutes.toString().padStart(2, '0');
  const ss = secs.toString().padStart(2, '0');
  const ms = milliseconds.toString().padStart(3, '0');

  return `${hh}H ${mm}:${ss}.${ms}`;
}
