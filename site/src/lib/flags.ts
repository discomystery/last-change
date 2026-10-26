// Countries as the NHL writes them (three letters, mostly ISO) to the two-letter codes flag drawings and country names
// use. A country missing here gets its name from the NHL's code and no flag; add it if one turns up.
export const ISO2: Record<string, string> = {
  CAN: 'ca', USA: 'us', SWE: 'se', FIN: 'fi', RUS: 'ru', CZE: 'cz', SVK: 'sk', CHE: 'ch', DEU: 'de', GER: 'de', DNK: 'dk',
  LVA: 'lv', BLR: 'by', NOR: 'no', AUT: 'at', FRA: 'fr', UKR: 'ua', SVN: 'si', AUS: 'au', KAZ: 'kz', GBR: 'gb', NLD: 'nl',
  ITA: 'it', POL: 'pl', LTU: 'lt', EST: 'ee', HUN: 'hu', HRV: 'hr', SRB: 'rs', JPN: 'jp', KOR: 'kr', CHN: 'cn', TWN: 'tw',
  BRA: 'br', VEN: 've', JAM: 'jm', NGA: 'ng', ZAF: 'za', BHS: 'bs', HTI: 'ht', IRL: 'ie', BEL: 'be', ROU: 'ro', BGR: 'bg',
  GRC: 'gr', ISR: 'il', LBN: 'lb', MEX: 'mx', ARG: 'ar', COL: 'co', PHL: 'ph', IDN: 'id', THA: 'th', VNM: 'vn', IND: 'in',
  NZL: 'nz', ISL: 'is', SCO: 'gb-sct', ENG: 'gb-eng', WAL: 'gb-wls', NIR: 'gb-nir', TTO: 'tt', BRB: 'bb', GUY: 'gy',
  KEN: 'ke', GHA: 'gh', EGY: 'eg', TUR: 'tr', BIH: 'ba', MKD: 'mk', MNE: 'me', ESP: 'es', PRT: 'pt', LUX: 'lu', SGP: 'sg',
};

// Short everyday names where the long official one would read oddly on a bio line.
const SHORT: Record<string, string> = { us: 'USA', gb: 'UK', kr: 'South Korea', 'gb-sct': 'Scotland', 'gb-eng': 'England', 'gb-wls': 'Wales', 'gb-nir': 'Northern Ireland' };
const NAMES = new Intl.DisplayNames(['en'], { type: 'region' });

export const countryName = (nhl: string): string => {
  const cc = ISO2[nhl];
  if (!cc) return nhl;
  if (SHORT[cc]) return SHORT[cc];
  try {
    return NAMES.of(cc.toUpperCase()) ?? nhl;
  } catch {
    return nhl;
  }
};

/** The flag drawing's path under the site (built from the flag-icons package by pages/flags/[cc].svg.ts), or null. */
export const flagPath = (nhl: string): string | null => (ISO2[nhl] ? `flags/${ISO2[nhl]}.svg` : null);
