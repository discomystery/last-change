// Ice time is always written minutes:seconds, the way hockey does it.
export const clock = (seconds: number) => {
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};

/**
 * Compare a percentile with a group from the side the value is on, the way people talk:
 * 89 → "busier than 89% of goalies", 11 → "quieter than 89% of goalies", 45 to 55 → "about average among goalies".
 * `up` and `down` are the two directions ("busier than" / "quieter than"). Capitalized unless `lower` is set.
 */
export function compared(pct: number, up: string, down: string, group: string, lower = false) {
  const p = Math.round(pct);
  const s = p >= 45 && p <= 55 ? `about average among ${group}` : p > 55 ? `${up} ${p}% of ${group}` : `${down} ${100 - p}% of ${group}`;
  return lower ? s : s[0].toUpperCase() + s.slice(1);
}

/** 1st, 2nd, 3rd, 4th ... 11th, 12th, 13th ... 21st. */
export const ordinal = (n: number) => {
  const s = ['th', 'st', 'nd', 'rd'], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
};
