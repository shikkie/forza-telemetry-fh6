export function formatDate(dateValue) {
  if (!dateValue) return '—';

  // Handle MongoDB extended JSON: { $date: "..." } or plain string / Date
  let dateStr = dateValue;
  if (typeof dateValue === 'object' && dateValue !== null) {
    if (dateValue.$date) {
      dateStr = dateValue.$date;
    } else if (dateValue instanceof Date) {
      dateStr = dateValue.toISOString();
    }
  }

  const date = new Date(dateStr);
  if (isNaN(date.getTime())) {
    return 'Invalid Date';
  }
  return date.toLocaleString();
}

export function formatTime(dateValue) {
  if (!dateValue) return '—';
  let dateStr = dateValue;
  if (typeof dateValue === 'object' && dateValue !== null && dateValue.$date) {
    dateStr = dateValue.$date;
  }
  const date = new Date(dateStr);
  if (isNaN(date.getTime())) return 'Invalid';
  return date.toLocaleTimeString();
}
