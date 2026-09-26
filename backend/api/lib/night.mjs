// A "night" of karaoke runs until 6am local time, so a request at 1am on Saturday
// belongs to Friday's night.
export const ROLLOVER_HOUR = 6;

export function nightDate(now = new Date(), timeZone = 'America/Los_Angeles') {
  const shifted = new Date(now.getTime() - ROLLOVER_HOUR * 3600 * 1000);
  return new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(shifted);
}
