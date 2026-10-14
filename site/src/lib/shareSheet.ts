// The share sheet every page uses (components/ShareDialog.astro holds its markup): shows the card as it is made, with a
// Light/Dark switch, then hands the picture to the phone's own share sheet or downloads it on a computer. Each page
// says what to make; the card maker itself (lib/shareCard.ts) loads on the first tap, not with the page.
export type Theme = 'light' | 'dark';
export type Ask = { title: string; file: string; key: string; make: (theme: Theme) => Promise<Blob> };

let ready: ((a: Ask) => void) | null = null;

function setup() {
  const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
  const scrim = $('sh-scrim'), img = $<HTMLImageElement>('sh-img'), wait = $('sh-wait'), go = $<HTMLButtonElement>('sh-go'), title = $('sh-t');
  // Phones hand the picture to their own share sheet; computers download it.
  const probe = () => { try { return !!navigator.canShare?.({ files: [new File([''], 'x.png', { type: 'image/png' })] }); } catch { return false; } };
  const phone = matchMedia('(pointer: coarse)').matches && probe();
  go.textContent = phone ? 'Share' : 'Download image';

  let ask: Ask | null = null, theme: Theme = 'light', made: { blob: Blob; url: string; file: string } | null = null, turn = 0;
  const shots = new Map<string, Promise<Blob>>();
  const make = (a: Ask, t: Theme) => {
    const key = `${a.key}:${t}`;
    if (!shots.has(key)) {
      const p = a.make(t);
      p.catch(() => shots.delete(key));
      shots.set(key, p);
    }
    return shots.get(key)!;
  };
  const paint = async () => {
    const me = ++turn;
    document.querySelectorAll<HTMLButtonElement>('.sh-seg button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.c === theme)));
    img.hidden = true; wait.hidden = false; wait.textContent = 'Making the card…'; go.disabled = true;
    try {
      const blob = await make(ask!, theme);
      if (me !== turn) return;
      if (made) URL.revokeObjectURL(made.url);
      made = { blob, url: URL.createObjectURL(blob), file: ask!.file };
      img.src = made.url; img.hidden = false; wait.hidden = true; go.disabled = false;
    } catch {
      if (me === turn) wait.textContent = 'The card couldn’t be made. Please try again.';
    }
  };
  const close = () => { scrim.hidden = true; turn++; document.documentElement.style.overflow = ''; };
  document.addEventListener('click', (e) => {
    const el = e.target instanceof Element ? e.target : null;
    const c = el?.closest<HTMLButtonElement>('.sh-seg button');
    if (c) { theme = c.dataset.c as Theme; paint(); return; }
    if (el === scrim || el?.closest('#sh-x')) close();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !scrim.hidden) close(); });
  go.addEventListener('click', async () => {
    if (!made) return;
    const file = new File([made.blob], made.file, { type: 'image/png' });
    if (phone && navigator.canShare?.({ files: [file] })) {
      try { await navigator.share({ files: [file] }); } catch { /* closed without sharing */ }
      return;
    }
    const a = document.createElement('a');
    a.href = made.url; a.download = made.file;
    document.body.appendChild(a); a.click(); a.remove();
  });
  return (a: Ask) => {
    ask = a; title.textContent = a.title;
    theme = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    scrim.hidden = false; document.documentElement.style.overflow = 'hidden';
    paint();
  };
}

/** Opens the sheet and starts making the card. */
export function openShare(a: Ask) {
  ready ??= setup();
  ready(a);
}

/** The season reading the visitor has picked in the settings: blended (default) or this season only. */
export const mode = () => (document.documentElement.dataset.mode === 'season' ? 'season' : 'blend') as 'blend' | 'season';

export const slug = (s: string) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

/** Every Share link on the page with this kind calls `fn` with its button. */
export function onShare(kind: string, fn: (b: HTMLElement) => void) {
  document.addEventListener('click', (e) => {
    const b = e.target instanceof Element ? e.target.closest<HTMLElement>('.share-btn') : null;
    if (b && b.dataset.share === kind) fn(b);
  });
}
